from importlib import import_module
import json
import argparse
import sys
import time

import logging

from saml2.config import SPConfig

from idp_test.base import FatalError
from idp_test.base import do_sequence
from idp_test.httpreq import HTTPC
#from saml2.config import Config
from saml2.mdstore import MetadataStore, MetaData

# Schemas supported
from saml2 import md
from saml2 import saml
from saml2.extension import mdui
from saml2.extension import idpdisc
from saml2.extension import dri
from saml2.extension import mdattr
from saml2.extension import ui
from saml2.metadata import entity_descriptor
import xmldsig
import xmlenc

SCHEMA = [ dri, idpdisc, md, mdattr, mdui, saml, ui, xmldsig, xmlenc]

__author__ = 'rolandh'

import traceback

logger = logging.getLogger("")

def exception_trace(tag, exc, log=None):
    message = traceback.format_exception(*sys.exc_info())
    if log:
        log.error("[%s] ExcList: %s" % (tag, "".join(message),))
        log.error("[%s] Exception: %s" % (tag, exc))
    else:
        print >> sys.stderr, "[%s] ExcList: %s" % (tag, "".join(message),)
        print >> sys.stderr, "[%s] Exception: %s" % (tag, exc)

class Trace(object):
    def __init__(self):
        self.trace = []
        self.start = time.time()

    def request(self, msg):
        delta = time.time() - self.start
        self.trace.append("%f --> %s" % (delta, msg))

    def reply(self, msg):
        delta = time.time() - self.start
        self.trace.append("%f <-- %s" % (delta, msg))

    def info(self, msg):
        delta = time.time() - self.start
        self.trace.append("%f %s" % (delta, msg))

    def error(self, msg):
        delta = time.time() - self.start
        self.trace.append("%f [ERROR] %s" % (delta, msg))

    def warning(self, msg):
        delta = time.time() - self.start
        self.trace.append("%f [WARNING] %s" % (delta, msg))

    def __str__(self):
        try:
            return "\n".join([t.encode("utf-8") for t in self.trace])
        except UnicodeDecodeError:
            arr = []
            for t in self.trace:
                try:
                    arr.append(t.encode("utf-8"))
                except UnicodeDecodeError:
                    arr.append(t)
        return "\n".join(arr)

    def clear(self):
        self.trace = []

    def __getitem__(self, item):
        return self.trace[item]

    def next(self):
        for line in self.trace:
            yield line

class SAML2client(object):

    def __init__(self, operations):
        self.trace = Trace()
        self.operations = operations

        self._parser = argparse.ArgumentParser()
        self._parser.add_argument('-d', dest='debug', action='store_true',
                                  help="Print debug information")
        self._parser.add_argument('-v', dest='verbose', action='store_true',
                                  help="Print runtime information")
        self._parser.add_argument('-C', dest="ca_certs",
                                  help="CA certs to use to verify HTTPS server certificates, if HTTPS is used and no server CA certs are defined then no cert verification will be done")
        self._parser.add_argument('-J', dest="json_config_file",
                                  help="Script configuration")
        self._parser.add_argument('-S', dest="sp_id", help="SP id")
        self._parser.add_argument("-s", dest="list_sp_id", action="store_true",
                                  help="List all the SP variants as a JSON object")
        self._parser.add_argument('-m', dest="metadata", action='store_true',
                                  help="Return the SP metadata")
        self._parser.add_argument("-l", dest="list", action="store_true",
                                  help="List all the test flows as a JSON object")
        self._parser.add_argument("oper", nargs="?", help="Which test to run")

        self.interactions = None
        self.entity_id = None
        self.sp_config = None

    def json_config_file(self):
        if self.args.json_config_file == "-":
            return json.loads(sys.stdin.read())
        else:
            return json.loads(open(self.args.json_config_file).read())

    def sp_configure(self, metadata_construction=False):
        sys.path.insert(0, ".")
        mod = import_module("config_file")
        if self.args.sp_id is None:
            if len(mod.CONFIG) == 1:
                self.args.sp_id = mod.CONFIG.keys()[0]
            else:
                raise Exception("SP id undefined")

        self.sp_config = SPConfig().load(mod.CONFIG[self.args.sp_id],
                                         metadata_construction)

    def setup(self):
        self.json_config= self.json_config_file()

        _jc = self.json_config

        self.interactions = _jc["interaction"]
        self.entity_id = _jc["entity_id"]

        self.sp_configure()

        metadata = MetadataStore(SCHEMA, self.sp_config.attribute_converters,
                                 self.sp_config.xmlsec_binary)
        metadata[0] = MetaData(SCHEMA, self.sp_config.attribute_converters,
                               _jc["metadata"])
        self.sp_config.metadata = metadata

    def test_summation(self, id):
        status = 0
        for item in self.test_log:
            if item["status"] > status:
                status = item["status"]

        if status == 0:
            status = 1

        sum = {
            "id": id,
            "status": status,
            "tests": self.test_log
        }

        if status == 5:
            sum["url"] = self.test_log[-1]["url"]
            sum["htmlbody"] = self.test_log[-1]["message"]

        return sum

    def run(self):
        self.args = self._parser.parse_args()

        if self.args.metadata:
            return self.make_meta()
        elif self.args.list_sp_id:
            return self.list_conf_id()
        elif self.args.list:
            return self.list_operations()
        else:
            if not self.args.oper:
                raise Exception("Missing test case specification")
            self.args.oper = self.args.oper.strip("'")
            self.args.oper = self.args.oper.strip('"')

        self.setup()

        try:
            try:
                oper = self.operations.OPERATIONS[self.args.oper]
            except KeyError:
                print >> sys.stderr, "Undefined testcase"
                return

            testres, trace = do_sequence(self.sp_config, oper, HTTPC(),
                                         self.trace, self.interactions,
                                         entity_id=self.json_config["entity_id"])
            self.test_log = testres
            sum = self.test_summation(self.args.oper)
            print >>sys.stdout, json.dumps(sum)
            if sum["status"] > 1 or self.args.debug:
                print >> sys.stderr, trace
        except FatalError:
            pass
        except Exception, err:
            print >> sys.stderr, self.trace
            print err
            exception_trace("RUN", err)

    def list_operations(self):
        lista = []
        for key,val in self.operations.OPERATIONS.items():
            item = {"id": key,
                    "name": val["name"],}
            try:
                _desc = val["descr"]
                if isinstance(_desc, basestring):
                    item["descr"] = _desc
                else:
                    item["descr"] = "\n".join(_desc)
            except KeyError:
                pass

            for key in ["depends", "endpoints"]:
                try:
                    item[key] = val[key]
                except KeyError:
                    pass

            lista.append(item)
        print json.dumps(lista)

    def _get_operation(self, operation):
        return self.operations.OPERATIONS[operation]

    def make_meta(self):
        self.sp_configure(True)
        print entity_descriptor(self.sp_config)

    def list_conf_id(self):
        sys.path.insert(0, ".")
        mod = import_module("config_file")
        _res = dict([(key, cnf["description"]) for key, cnf in mod.CONFIG.items()])
        print json.dumps(_res)