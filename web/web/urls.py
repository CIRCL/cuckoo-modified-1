# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

from django.conf.urls import patterns, include, url

from dashboard import views as dashboard_views
from analysis import views as analysis_views

from dashboard import urls as dashboard
from analysis import urls as analysis
from compare import urls as compare
from submission import urls as submission
from api import urls as api

urlpatterns = [
    url(r"^$", dashboard_views.index),
    url(r"^analysis/", include(analysis)),
    url(r"^compare/", include(compare)),
    url(r"^submit/", include(submission)),
    url(r"^file/(?P<category>\w+)/(?P<task_id>\d+)/(?P<dlfile>\w+)/$", analysis_views.file),
    url(r"^filereport/(?P<task_id>\w+)/(?P<category>\w+)/$", analysis_views.filereport),
    url(r"^full_memory/(?P<analysis_number>\w+)/$", analysis_views.full_memory_dump_file),
    url(r"^full_memory_strings/(?P<analysis_number>\w+)/$", analysis_views.full_memory_dump_strings),
    url(r"^dashboard/", include(dashboard)),
    url(r"^api/", include(api)),
]
