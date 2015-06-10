#!/usr/bin/env python
# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import os
import sys
import time
import logging
import argparse
import signal
import multiprocessing

logging.basicConfig(level=logging.INFO)
log = logging.getLogger()

sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))

from bson.objectid import ObjectId
from gridfs import GridFS
from lib.cuckoo.common.config import Config
from lib.cuckoo.common.constants import CUCKOO_ROOT
from lib.cuckoo.core.database import Database, TASK_REPORTED, TASK_COMPLETED
from lib.cuckoo.core.database import TASK_FAILED_PROCESSING
from lib.cuckoo.core.plugins import GetFeeds, RunProcessing, RunSignatures
from lib.cuckoo.core.plugins import RunReporting
from lib.cuckoo.core.startup import init_modules
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

def process(target=None, copy_path=None, task=None, report=False, auto=False):
    results = RunProcessing(task=task).run()
    # This is the results container. It's what will be used by all the
    # reporting modules to make it consumable by humans and machines.
    # It will contain all the results generated by every processing
    # module available. Its structure can be observed through the JSON
    # dump in the analysis' reports folder. (If jsondump is enabled.)
    results = { }
    results["statistics"] = { }
    results["statistics"]["processing"] = list()
    results["statistics"]["signatures"] = list()
    results["statistics"]["reporting"] = list()
    GetFeeds(results=results).run()
    RunProcessing(task=task, results=results).run()
    RunSignatures(task=task, results=results).run()

    if report:
        mongoconf = Config("reporting").mongodb
        if mongoconf["enabled"]:
            host = mongoconf["host"]
            port = mongoconf["port"]
            db = mongoconf["db"]
            conn = MongoClient(host, port)
            mdata = conn[db]
            fs = GridFS(mdata)
            analyses = mdata.analysis.find({"info.id": int(task_id)})
            if analyses.count() > 0:
                log.debug("Deleting analysis data for Task %s" % task_id)
                for analysis in analyses:
                    if "file_id" in analysis["target"]:
                        if mdata.analysis.find({"target.file_id": ObjectId(analysis["target"]["file_id"])}).count() == 1:
                            fs.delete(ObjectId(analysis["target"]["file_id"]))
                    for shot in analysis["shots"]:
                        if mdata.analysis.find({"shots": ObjectId(shot)}).count() == 1:
                            fs.delete(ObjectId(shot))
                    if "pcap_id" in analysis["network"] and mdata.analysis.find({"network.pcap_id": ObjectId(analysis["network"]["pcap_id"])}).count() == 1:
                        fs.delete(ObjectId(analysis["network"]["pcap_id"]))
                    if "sorted_pcap_id" in analysis["network"] and mdata.analysis.find({"network.sorted_pcap_id": ObjectId(analysis["network"]["sorted_pcap_id"])}).count() == 1:
                        fs.delete(ObjectId(analysis["network"]["sorted_pcap_id"]))
                    for drop in analysis["dropped"]:
                        if "object_id" in drop and mdata.analysis.find({"dropped.object_id": ObjectId(drop["object_id"])}).count() == 1:
                            fs.delete(ObjectId(drop["object_id"]))
                    for process in analysis["behavior"]["processes"]:
                        for call in process["calls"]:
                            mdata.calls.remove({"_id": ObjectId(call)})
                    mdata.analysis.remove({"_id": ObjectId(analysis["_id"])})
            conn.close()
            log.debug("Deleted previous MongoDB data for Task %s" % task_id)

        RunReporting(task=task, results=results).run()
        Database().set_status(task_id, TASK_REPORTED)

        if auto:
            if cfg.cuckoo.delete_original and os.path.exists(target):
                os.unlink(target)

            if cfg.cuckoo.delete_bin_copy and os.path.exists(copy_path):
                os.unlink(copy_path)

def init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

def autoprocess(parallel=1):
    maxcount = cfg.cuckoo.max_analysis_count
    count = 0
    db = Database()
    pool = multiprocessing.Pool(parallel, init_worker)
    pending_results = []

    try:
        # CAUTION - big ugly loop ahead.
        while count < maxcount or not maxcount:

            # Pending_results maintenance.
            for ar, tid, target, copy_path in list(pending_results):
                if ar.ready():
                    if ar.successful():
                        log.info("Task #%d: reports generation completed", tid)
                    else:
                        try:
                            ar.get()
                        except:
                            log.exception("Exception when processing task ID %u.", tid)
                            db.set_status(tid, TASK_FAILED_PROCESSING)

                    pending_results.remove((ar, tid, target, copy_path))

            # If still full, don't add more (necessary despite pool).
            if len(pending_results) >= parallel:
                time.sleep(5)
                continue

            # If we're here, getting parallel tasks should at least
            # have one we don't know.
            tasks = db.list_tasks(status=TASK_COMPLETED, limit=parallel,
                                  order_by="completed_on asc")

            added = False
            # For loop to add only one, nice. (reason is that we shouldn't overshoot maxcount)
            for task in tasks:
                # Not-so-efficient lock.
                if task.id in [tid for ar, tid, target, copy_path
                               in pending_results]:
                    continue

                log.info("Processing analysis data for Task #%d", task.id)

                if task.category == "file":
                    sample = db.view_sample(task.sample_id)

                    copy_path = os.path.join(CUCKOO_ROOT, "storage",
                                             "binaries", sample.sha256)
                else:
                    copy_path = None

                args = task.target, copy_path
                kwargs = dict(report=True, auto=True, task=task.to_dict())
                result = pool.apply_async(process, args, kwargs)

                pending_results.append((result, task.id, task.target, copy_path))

                count += 1
                added = True
                break

            if not added:
                # don't hog cpu
                time.sleep(5)

    except KeyboardInterrupt:
        pool.terminate()
        raise
    except:
        import traceback
        traceback.print_exc()
    finally:
        pool.join()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("id", type=str, help="ID of the analysis to process (auto for continuous processing of unprocessed tasks).")
    parser.add_argument("-d", "--debug", help="Display debug messages", action="store_true", required=False)
    parser.add_argument("-r", "--report", help="Re-generate report", action="store_true", required=False)
    parser.add_argument("-p", "--parallel", help="Number of parallel threads to use (auto mode only).", type=int, required=False, default=1)
    args = parser.parse_args()

    if args.debug:
        log.setLevel(logging.DEBUG)

    logging.getLogger("urllib3").setLevel(logging.WARNING)

    init_modules()

    if args.id == "auto":
        autoprocess(parallel=args.parallel)
    else:
        task = Database().view_task(int(args.id))
        process(task=task.to_dict(), report=args.report)

if __name__ == "__main__":
    cfg = Config()

    try:
        main()
    except KeyboardInterrupt:
        pass
