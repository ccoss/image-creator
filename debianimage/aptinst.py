#
# yum.py : yum utilities
#
# Copyright 2007, Red Hat  Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

import glob
import os
import sys
import logging
import apt

import pykickstart.parser

from imgcreate.errors import *

class TextProgress(object):
    logger = logging.getLogger()
    def emit(self, lvl, msg):
        '''play nice with the logging module'''
        for hdlr in self.logger.handlers:
            if lvl >= self.logger.level:
                hdlr.stream.write(msg)
                hdlr.stream.flush()

    def start(self, filename, url, *args, **kwargs):
        self.emit(logging.INFO, "Retrieving %s " % (url,))
        self.url = url
    def update(self, *args):
        pass
    def end(self, *args):
        self.emit(logging.INFO, "...OK\n")

class Debootstrap(object):
    """class for debootstrap"""
    def __init__(self, rootdir):
        self.rootdir = rootdir
        self.repoCache = apt.Cache( None, self.rootdir )
        self.repos = []

    def addRepo(fullurl):
        self.repos.appned("deb " + fullurl)

    def _writeSourcesList():
        sourcesListPath = self.rootdir + "/etc/apt/sources.list"
        sourcesList = "\n".join(self.repos)

        f = file(sourcesListPath, "w+")
        f.write(sourcesList)
        f.close()

        os.chmod(sourcesListPath, 0644)

    def setup():
        self._writeSourcesList()
        self.repoCache.update()
        self.repoCache.open()

    def findPackages():
        pass

    def downloadPackages():
        pass

    def installDpkg():
        pass

    def installCore():
        pass

    def installBase():
        pass

    def debootstrp():
        findPackages()
        downloadPackages()
        installDpkg()
        installCore()
        installBase()


class Apt(object):
    def __init__(self, releasever=None):
        """
        releasever = optional value to use in replacing $releasever in repos
        """
        self.releasever = releasever
        self.extraPackages = []

    def doFileLogSetup(self, uid, logfile):
        # don't do the file log for the livecd as it can lead to open fds
        # being left and an inability to clean up after ourself
        pass

    def close(self):
        pass

    def __del__(self):
        pass

    def _writeConf(self, confpath, arch):
        conf  = "[main]\n"
        conf += "installroot=%s\n" % installroot
        conf += "cachedir=/var/cache/yum\n"
        conf += "plugins=0\n"
        conf += "reposdir=\n"
        conf += "failovermethod=priority\n"
        conf += "keepcache=1\n"

        f = file(confpath, "w+")
        f.write(conf)
        f.close()

        os.chmod(confpath, 0644)


    def setup(self, confpath, installroot):
        self._writeConf(confpath, installroot)
        self.installer = Debootstrap(installroot)

    def selectPackage(self, pkg):
        """Select a given package.  Can be specified with name.arch or name*"""
        if pkg not in self.installer.instPackages() and self.installer.hasPackage(pkg):
            self.extraPackages.append(pkg)
        
    def deselectPackage(self, pkg):
        """Deselect package.  Can be specified as name.arch or name*"""
        if self.installer.hasPackage(pkg):
           self.extraPackages.remove(pkg)


    def selectGroup(self, grp, include = pykickstart.parser.GROUP_DEFAULT):
        pass

    def addRepository(self, fullurl):
        self.installer.addRepo(fullurl)
            
    def runInstall(self):
        os.environ["HOME"] = "/"
        self.installer.setup()
        self.installer.debootstrap()
        #apt-get install self.extraPackages -y --force-yes
