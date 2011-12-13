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
from appcreate.appliance import *
from appcreate.partitionedfs import *
import urlgrabber.progress as progress

from debianimage.aptinst import *
from debianimage import kickstart

class DebApplianceImageCreator(ApplianceImageCreator):
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
#        ImageCreator.__init__(self, ks, name)
        ApplianceImageCreator.__init__(self, ks, name, disk_format, vmem, vcpu)

        self.__instloop = None
        self.__imgdir = None
        self.__disks = {}
        self.__disk_format = disk_format
        
        #appliance parameters 
        self.vmem = vmem
        self.vcpu = vcpu
        self.checksum = False
        self.appliance_version = None
        self.appliance_release = None
        self.arch = None
        
        #additional modules to include   
#        self.modules = ["sym53c8xx", "aic7xxx", "mptspi"]
#        self.modules.extend(kickstart.get_modules(self.ks))
        
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

        self.__create_selinuxfs()

        self._ImageCreator__create_minimal_dev()

        os.symlink("/proc/self/mounts", self._instroot + "/etc/mtab")

        self._ImageCreator__write_fstab()

    def __create_selinuxfs(self):
        pass

    
    def _create_mkinitrd_config(self):
        #write  to tell which modules to be included in initrd
        pass

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


    def _get_required_packages(self):
        return ["grub-legacy"]

    def _create_grub_devices(self):
        devs = []
        parts = kickstart.get_partitions(self.ks)
        for p in parts:
            dev = p.disk
            if not dev in devs:
                devs.append(dev)

        devs.sort()

        n = 0
        devmap = ""
        for dev in devs:
            devmap += "(hd%-d) /dev/%s\n" % (n, dev)
            n += 1

        logging.debug("Writing grub %s/boot/grub/device.map" % self._instroot)
        makedirs(self._instroot + "/boot/grub/")
        cfg = open(self._instroot + "/boot/grub/device.map", "w")
        cfg.write(devmap)
        cfg.close()

    def _create_grub_config(self):
        (bootdevnum, rootdevnum, rootdev, prefix) = self._get_grub_boot_config()
        options = self.ks.handler.bootloader.appendLine

        # NB we're assuming that grub config is on the first physical disk
        # ie /boot must be on sda, or if there's no /boot, then / must be sda

        # XXX don't hardcode default kernel - see livecd code
        grub = ""
        grub += "default=0\n"
        grub += "timeout=5\n"
#        grub += "splashimage=(hd0,%d)%s/grub/splash.xpm.gz\n" % (bootdevnum, prefix)
        grub += "hiddenmenu\n"

        versions = []
        kernels = self._get_kernel_versions()
        for kernel in kernels:
            for version in kernels[kernel]:
                versions.append(version)

        for v in versions:
            grub += "title %s (%s)\n" % (self.name, v)
            grub += "        root (hd0,%d)\n" % bootdevnum
            grub += "        kernel %s/vmlinuz-%s quiet root=%s %s\n" % (prefix, v, rootdev, options)
            grub += "        initrd %s/initrd.img-%s\n" % (prefix, v)

        logging.debug("Writing grub config %s/boot/grub/menu.lst" % self._instroot)
        cfg = open(self._instroot + "/boot/grub/menu.lst", "w")
        cfg.write(grub)
        cfg.close()

    def _copy_grub_files(self):
        imgpath = None
        for machine in ["x86_64-pc", "i386-pc"]:
            imgpath = self._instroot + "/usr/lib/grub/" + machine
            if os.path.exists(imgpath):
                break

        files = ["e2fs_stage1_5", "stage1", "stage2"]
        for f in files:
            path = imgpath + "/" + f
            if not os.path.isfile(path):
                raise CreatorError("grub not installed : "
                                   "%s not found" % path)

            logging.debug("Copying %s to %s/boot/grub/%s" %(path, self._instroot, f))
            shutil.copy(path, self._instroot + "/boot/grub/" + f)


    def _get_kernel_versions(self):
        import glob

        ret = {}
        kernel_files = glob.glob(self._instroot + "/boot/vmlinuz-*")
        if len(kernel_files) > 0:
            ret['vmlinuz'] = []
            for f in kernel_files:
                ret['vmlinuz'].append(f.split("vmlinuz-")[1])

        kernel_files = glob.glob(self._instroot + "/boot/vmlinux-*")
        if len(kernel_files) > 0:
            ret['vmlinux'] = []
            for f in kernel_files:
                ret['vmlinux'].append(f.split("vmlinux-")[1])

        return ret

