#
# appliance.py: ApplianceImageCreator class
#
# Copyright 2007-2008, Red Hat  Inc.
# Copyright 2008, Daniel P. Berrange
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

import os
import os.path
import glob
import shutil
import zipfile
import tarfile
import subprocess
import logging

from imgcreate.errors import *
from imgcreate.fs import *
from imgcreate.creator import *
from appcreate.partitionedfs import *
import urlgrabber.progress as progress

from debianimage.aptinst import *
from debianimage import kickstart


class TarImageCreator(ImageCreator):
    """Installs a system into a file containing a partitioned disk image.

    ApplianceImageCreator is an advanced ImageCreator subclass; a sparse file
    is formatted with a partition table, each partition loopback mounted
    and the system installed into an virtual disk. The disk image can
    subsequently be booted in a virtual machine or accessed with kpartx

    """

    def __init__(self, ks, name, disk_format, vmem, vcpu):
        """Initialize a ApplianceImageCreator instance.

        This method takes the same arguments as ImageCreator.__init__()

        """
        ImageCreator.__init__(self, ks, name)

        self.appliance_version = None
        self.appliance_release = None
        self.arch = None
        

    def _get_fstab(self):
        s = ""

        s += "devpts     /dev/pts  devpts  gid=5,mode=620   0 0\n"
        s += "tmpfs      /dev/shm  tmpfs   defaults         0 0\n"
        s += "proc       /proc     proc    defaults         0 0\n"
        s += "sysfs      /sys      sysfs   defaults         0 0\n"
        return s
    
    
    #
    # Actual implementation
    #
    def _mount_instroot(self, base_on = None):
        pass

    def _get_required_packages(self):
        return []

    def _create_bootconfig(self):
        pass

    def _unmount_instroot(self):
        pass
    
    def package(self, destdir,package,include):
        """Prepares the created image for final delivery.
           Stage
           add includes
           package
        """
        # make tarball  of self._instroot with --numeric-owner
        curdir = os.getcwd()
        dst = "%s/%s.tar" % (curdir, self.name)    
        os.chdir(self._instroot)
        commstr = "tar cf %s *  --numeric-owner " % dst
        logging.info("creating %s, command:%s" %  (dst, commstr))
        #print "creating %s, command:%s" %  (dst, commstr)
        os.system(commstr)
        os.chdir(curdir)

        
    def mount(self, base_on = None, cachedir = None):
        """Setup the target filesystem in preparation for an install.

        This function sets up the filesystem which the ImageCreator will
        install into and configure. The ImageCreator class merely creates an
        install root directory, bind mounts some system directories (e.g. /dev)
        and writes out /etc/fstab. Other subclasses may also e.g. create a
        sparse file, format it and loopback mount it to the install root.

        base_on -- a previous install on which to base this install; defaults
                   to None, causing a new image to be created

        cachedir -- a directory in which to store the Yum cache; defaults to
                    None, causing a new cache to be created; by setting this
                    to another directory, the same cache can be reused across
                    multiple installs.

        """

        self._ImageCreator__ensure_builddir()

        makedirs(self._instroot)
        makedirs(self._outdir)

        self._mount_instroot(base_on)

        for d in ("/dev/pts", "/etc", "/boot", "/var/log", "/sys", "/proc"):
            makedirs(self._instroot + d)

#        cachesrc = cachedir or (self.__builddir + "/yum-cache")
#        makedirs(cachesrc)

        # bind mount system directories into _instroot
        for (f, dest) in [("/sys", None), ("/proc", None),
                          ("/dev/pts", None), ("/dev/shm", None)]:
            if os.path.exists(f):
                self._ImageCreator__bindmounts.append(BindChrootMount(f, self._instroot, dest))
            else:
                logging.warn("Skipping (%s,%s) because source doesn't exist." % (f, dest))

        self._do_bindmounts()

        #self.__create_selinuxfs()

        self._ImageCreator__create_minimal_dev()

        os.symlink("/proc/self/mounts", self._instroot + "/etc/mtab")

        self._ImageCreator__write_fstab()
        
    def setArch( self, arch=None ):
        self.arch = arch
        
    def install(self, repo_urls = {}):
        aApt = Apt()
        aApt.setup( self._instroot, self.arch )
        for repo in kickstart.get_repos(self.ks, repo_urls):
            (name, baseurl, mirrorlist, proxy, inc, exc) = repo
            aApt.addRepository( baseurl )

        for pkg in kickstart.get_packages(self.ks,
                                          self._get_required_packages()):
            aApt.selectPackage( pkg )

        aApt.runInstall()
    
    def configure(self):
        """Configure the system image according to the kickstart.

        This method applies the (e.g. keyboard or network) configuration
        specified in the kickstart and executes the kickstart %post scripts.

        If neccessary, it also prepares the image to be bootable by e.g.
        creating an initrd and bootloader configuration.

        """
        ksh = self.ks.handler

        kickstart.LanguageConfig(self._instroot).apply(ksh.lang)
        kickstart.KeyboardConfig(self._instroot).apply(ksh.keyboard)
        kickstart.TimezoneConfig(self._instroot).apply(ksh.timezone)
        kickstart.AuthConfig(self._instroot).apply(ksh.authconfig)
        kickstart.FirewallConfig(self._instroot).apply(ksh.firewall)
        kickstart.RootPasswordConfig(self._instroot).apply(ksh.rootpw)
        kickstart.ServicesConfig(self._instroot).apply(ksh.services)
        kickstart.XConfig(self._instroot).apply(ksh.xconfig)
        kickstart.NetworkConfig(self._instroot).apply(ksh.network)
#        kickstart.RPMMacroConfig(self._instroot).apply(self.ks)

        self._create_bootconfig()

        self._ImageCreator__run_post_scripts()
#        kickstart.SelinuxConfig(self._instroot).apply(ksh.selinux)

        
               

