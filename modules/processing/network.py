# Copyright (C) 2010-2012 Cuckoo Sandbox Developers.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import os
import re
import sys
import socket
import logging
from urlparse import urlunparse

from lib.cuckoo.common.utils import convert_to_printable
from lib.cuckoo.common.abstracts import Processing

try:
    import dpkt
    IS_DPKT = True
except ImportError, why:
    IS_DPKT = False

class Pcap:
    """
    Network PCAP.
    """
    
    def __init__(self, filepath):
        """
        Creates a new instance.
        @param filepath: path to PCAP file
        """ 
        self.filepath = filepath
        
        # List containing all IP addresses involved in the analysis.
        self.unique_hosts = []
        # List containing all TCP packets.
        self.tcp_connections = []
        # List containing all UDP packets.
        self.udp_connections = []
        # List containing all HTTP requests.
        self.http_requests = []
        # List containing all DNS requests.
        self.dns_requests = []
        # List used to track already added DNS request processing.
        # It's used to avoid processing and resolving the same domains
        # multiple times.
        self.dns_performed = []
        # Dictionary containing all the results of this processing.
        self.results = {}
    
    def _add_hosts(self, connection):
        """
        Add IPs to unique list.
        @param connection: connection data
        """
        try:
            if connection["src"] not in self.unique_hosts:
                self.unique_hosts.append(convert_to_printable(connection["src"]))
            if connection["dst"] not in self.unique_hosts:
                self.unique_hosts.append(convert_to_printable(connection["dst"]))
        except Exception, why:
            return False
        
        return True
        
    def _check_http(self, tcpdata):
        """
        Checks for HTTP traffic.
        @param tcpdata: tcp data flow
        """ 
        try:
            dpkt.http.Request(tcpdata)
        except dpkt.dpkt.UnpackError:
            return False
            
        return True
        
    def _add_http(self, tcpdata, dport):
        """
        Adds an HTTP flow.
        @param tcpdata: TCP data in flow
        @param dport: destination port
        """  
        http = dpkt.http.Request(tcpdata)

        try:
            entry = {}

            if "host" in http.headers:
                entry["host"] = convert_to_printable(http.headers["host"])
            else:
                entry["host"] = ""

            entry["port"] = dport
            entry["data"] = convert_to_printable(tcpdata)
            entry["uri"] = convert_to_printable(urlunparse(("http", entry["host"], http.uri, None, None, None)))
            entry["body"] = convert_to_printable(http.body)
            entry["path"] = convert_to_printable(http.uri)

            if "user-agent" in http.headers:
                entry["user-agent"] = convert_to_printable(http.headers["user-agent"])

            entry["version"] = convert_to_printable(http.version)
            entry["method"] = convert_to_printable(http.method)

            self.http_requests.append(entry)
        except Exception, why:
            return False

        return True
    
    def _check_dns(self, udpdata):
        """
        Checks for DNS traffic.
        @param udpdata: UDP data flow
        """ 
        try:
            dpkt.dns.DNS(udpdata)
        except:
            return False
        
        return True
    
    def _add_dns(self, udpdata):
        """
        Adds a DNS data flow.
        @param udpdata: data inside flow
        """ 
        dns = dpkt.dns.DNS(udpdata)
        name = dns.qd[0].name
        
        if name not in self.dns_performed:
            if re.search("in-addr.arpa", name):
                return False

            # This is generated by time-sync of the virtual machine.
            if name.strip() == "time.windows.com":
                return False
            
            entry = {}
            entry["hostname"] = name

            try:
                socket.setdefaulttimeout(10)
                ip = socket.gethostbyname(name)
            except socket.gaierror:
                ip = ""

            entry["ip"] = ip

            self.dns_requests.append(entry)
            self.dns_performed.append(name)
            
            return True
        return False
    
    def run(self):
        """
        Process PCAP.
        @return: dict with network analysis data
        """
        log = logging.getLogger("Processing.Pcap")
        
        if not IS_DPKT:
            log.error("Python DPKT is not installed, aborting PCAP analysis.")
            return None

        if not os.path.exists(self.filepath):
            log.warning("The PCAP file does not exist at path \"%s\"." % self.filepath)
            return None

        if os.path.getsize(self.filepath) == 0:
            log.error("The PCAP file at path \"%s\" is empty." % self.filepath)
            return None

        file = open(self.filepath, "rb")

        try:
            pcap = dpkt.pcap.Reader(file)
        except dpkt.dpkt.NeedData:
            log.error("Unable to read PCAP file at path \"%s\"." % self.filepath)
            return None

        for ts, buf in pcap:
            try:
                eth = dpkt.ethernet.Ethernet(buf)
                ip = eth.data
                
                connection = {}
                if isinstance(ip, dpkt.ip.IP):
                    connection["src"] = socket.inet_ntoa(ip.src)
                    connection["dst"] = socket.inet_ntoa(ip.dst)
                elif isinstance(ip, dpkt.ip6.IP6):
                    connection["src"] = socket.inet_ntop(socket.AF_INET6, ip.src)
                    connection["dst"] = socket.inet_ntop(socket.AF_INET6, ip.dst)
                
                self._add_hosts(connection)
                
                if ip.p == dpkt.ip.IP_PROTO_TCP:
                    
                    tcp = ip.data

                    if len(tcp.data) > 0:
                        if self._check_http(tcp.data):
                            self._add_http(tcp.data, tcp.dport)

                        connection["sport"] = tcp.sport
                        connection["dport"] = tcp.dport
                          
                        self.tcp_connections.append(connection)
                    else:
                        continue
                elif ip.p == dpkt.ip.IP_PROTO_UDP:
                    udp = ip.data

                    if len(udp.data) > 0:
                        if udp.dport == 53:
                            if self._check_dns(udp.data):
                                self._add_dns(udp.data)

                        connection["sport"] = udp.sport
                        connection["dport"] = udp.dport

                        self.udp_connections.append(connection)
                #elif ip.p == dpkt.ip.IP_PROTO_ICMP:
                    #icmp = ip.data
            except AttributeError, why:
                continue
            except dpkt.dpkt.NeedData, why:
                continue

        file.close()

        self.results["hosts"] = self.unique_hosts
        self.results["tcp"] = self.tcp_connections
        self.results["udp"] = self.udp_connections
        self.results["http"] = self.http_requests
        self.results["dns"] = self.dns_requests
        
        return self.results

class NetworkAnalysis(Processing):
    def run(self):
        self.key = "network"

        results = Pcap(self.pcap_path).run()

        return results
