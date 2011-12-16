#
# live.py : LiveImageCreator class for creating Live CD images
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

import os
import os.path
import glob
import shutil
import subprocess
import logging
import re

from imgcreate.errors import *
from imgcreate.fs import *
from imgcreate.live import *
from debianimage.aptinst import *
from debianimage import kickstart

class DebLiveImageCreatorBase(LiveImageCreatorBase):
    """A base class for LiveCD image creators.

    This class serves as a base class for the architecture-specific LiveCD
    image creator subclass, LiveImageCreator.

    LiveImageCreator creates a bootable ISO containing the system image,
    bootloader, bootloader configuration, kernel and initramfs.

    """

    def __init__(self, ks, name, fslabel=None, releasever=None, tmpdir="/tmp",
                 title="Linux", product="Linux"):
        """Initialise a LiveImageCreator instance.

        This method takes the same arguments as LoopImageCreator.__init__().

        """
        LoopImageCreator.__init__(self, ks, name,
                                  fslabel=fslabel,
                                  releasever=releasever,
                                  tmpdir=tmpdir)

        self.compress_type = "xz"
        """mksquashfs compressor to use."""

        self.skip_compression = False
        """Controls whether to use squashfs to compress the image."""

        self.skip_minimize = False
        """Controls whether an image minimizing snapshot should be created.

        This snapshot can be used when copying the system image from the ISO in
        order to minimize the amount of data that needs to be copied; simply,
        it makes it possible to create a version of the image's filesystem with
        no spare space.

        """

        self._timeout = kickstart.get_timeout(self.ks, 10)
        """The bootloader timeout from kickstart."""

        self._default_kernel = kickstart.get_default_kernel(self.ks, "kernel")
        """The default kernel type from kickstart."""

        self.__isodir = None

        self.__modules = ["=ata", "sym53c8xx", "aic7xxx", "=usb", "=firewire",
                          "=mmc", "=pcmcia", "mptsas", "udf", "virtio_blk",
                          "virtio_pci"]
        self.__modules.extend(kickstart.get_modules(self.ks))

        self._isofstype = "iso9660"
        self.base_on = False

        self.title = title
        self.product = product


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
           

    def setArch( self, arch=None ):
        self.arch = arch

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

    def __destroy_selinuxfs(self):
        pass

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

    def _create_bootconfig(self):
        """Configure the image so that it's bootable."""
        self._configure_bootloader(self.__ensure_isodir())

    def _mount_instroot(self, base_on = None):
#        pass
        self.base_on = True
        LoopImageCreator._mount_instroot(self, base_on)

    def _unmount_instroot(self):
#        pass
        LoopImageCreator._unmount_instroot(self)

    def __ensure_isodir(self):
        if self.__isodir is None:
            self.__isodir = self._mkdtemp("iso-")
        return self.__isodir

    def _stage_final_image(self):
        try:
            makedirs(self.__ensure_isodir() + "/live")

            self._resparse()

            if not self.skip_minimize:
                pass

            if self.skip_compression:
                shutil.move(self._image, self.__isodir + "/live/filesystem.ext3")
                if os.stat(self.__isodir + "/live/filesystem.ext3").st_size >= 4*1024*1024*1024:
                    self._isofstype = "udf"
                    logging.warn("Switching to UDF due to size of live/filesystem.ext3")
            else:
                instloop = DiskMount( LoopbackDisk(self._image,0), self._instroot)
                instloop.mount()
                mksquashfs(self._instroot,
                           self.__isodir + "/live/filesystem.squashfs",
                           self.compress_type)
                if os.stat(self.__isodir + "/live/filesystem.squashfs").st_size >= 4*1024*1024*1024:
                    self._isofstype = "udf"
                    logging.warn("Switching to UDF due to size of live/filesystem.squashfs")
                instloop.cleanup()

            self.__create_iso(self.__isodir)
        finally:
            shutil.rmtree(self.__isodir, ignore_errors = True)
            self.__isodir = None

    def __create_iso(self, isodir):
        iso = self._outdir + "/" + self.name + ".iso"

        args = ["/usr/bin/genisoimage",
                "-J", "-r",
                "-hide-rr-moved", "-hide-joliet-trans-tbl",
                "-V", self.fslabel,
                "-o", iso]

        args.extend(self._get_mkisofs_options(isodir))
        if self._isofstype == "udf":
            args.append("-allow-limited-size")

        args.append(isodir)

        if subprocess.call(args) != 0:
            raise CreatorError("ISO creation failed!")

        if os.path.exists("/usr/bin/isohybrid"):
            subprocess.call(["/usr/bin/isohybrid", iso])

#        self.__implant_md5sum(iso)


class x86DebLiveImageCreator(DebLiveImageCreatorBase):
    """ImageCreator for x86 machines"""
    def _get_mkisofs_options(self, isodir):
        return [ "-b", "isolinux/isolinux.bin",
                 "-c", "isolinux/boot.cat",
                 "-no-emul-boot", "-boot-info-table",
                 "-boot-load-size", "4" ]

    def _get_required_packages(self):
        return ["syslinux"] + LiveImageCreatorBase._get_required_packages(self)

    def _get_isolinux_stanzas(self, isodir):
        return ""

    def __find_syslinux_menu(self):
        for menu in ("vesamenu.c32", "menu.c32"):
            for dir in ("/usr/lib/syslinux/", "/usr/share/syslinux/"):
                if os.path.isfile(self._instroot + dir + menu):
                    return menu

        raise CreatorError("syslinux not installed : "
                           "no suitable *menu.c32 found")

    def __find_syslinux_mboot(self):
        #
        # We only need the mboot module if we have any xen hypervisors
        #
        if not glob.glob(self._instroot + "/boot/xen.gz*"):
            return None

        return "mboot.c32"

    def __copy_syslinux_files(self, isodir, menu, mboot = None):
        files = ["isolinux.bin", menu]
        if mboot:
            files += [mboot]

        for f in files:
            if os.path.exists(self._instroot + "/usr/lib/syslinux/" + f):
                path = self._instroot + "/usr/lib/syslinux/" + f
            elif os.path.exists(self._instroot + "/usr/share/syslinux/" + f):
                path = self._instroot + "/usr/share/syslinux/" + f
            if not os.path.isfile(path):
                raise CreatorError("syslinux not installed : "
                                   "%s not found" % path)

            shutil.copy(path, isodir + "/isolinux/")

    def __copy_syslinux_background(self, isodest):
        background_path = self._instroot + \
                          "/usr/share/anaconda/boot/syslinux-vesa-splash.jpg"

        if not os.path.exists(background_path):
            # fallback to F13 location
            background_path = self._instroot + \
                              "/usr/lib/anaconda-runtime/syslinux-vesa-splash.jpg"

            if not os.path.exists(background_path):
                return False

        shutil.copyfile(background_path, isodest)

        return True

    def __copy_kernel_and_initramfs(self, isodir, version, index):
        bootdir = self._instroot + "/boot"

        shutil.copyfile(bootdir + "/vmlinuz-" + version,
                        isodir + "/isolinux/vmlinuz" + index)

        isDracut = False
        if os.path.exists(bootdir + "/initramfs.img-" + version):
            shutil.copyfile(bootdir + "/initramfs.img-" + version,
                            isodir + "/isolinux/initrd" + index + ".img")
            isDracut = True
        elif os.path.exists(bootdir + "/initrd.img-" + version):
            shutil.copyfile(bootdir + "/initrd.img-" + version ,
                            isodir + "/isolinux/initrd" + index + ".img")
        elif not self.base_on:
            logging.error("No initrd or initramfs found for %s" % (version,))

        is_xen = False
        if os.path.exists(bootdir + "/xen.gz-" + version[:-3]):
            shutil.copyfile(bootdir + "/xen.gz-" + version[:-3],
                            isodir + "/isolinux/xen" + index + ".gz")
            is_xen = True

        return (is_xen, isDracut)

    def __is_default_kernel(self, kernel, kernels):
        if len(kernels) == 1:
            return True

        if kernel == self._default_kernel:
            return True

        if kernel.startswith("kernel-") and kernel[7:] == self._default_kernel:
            return True

        return False

    def __get_basic_syslinux_config(self, **args):
        return """
default %(menu)s
timeout %(timeout)d
menu background %(background)s
menu autoboot Starting %(title)s in # second{,s}. Press any key to interrupt.

menu clear
menu title %(title)s
menu vshift 8
menu rows 18
menu margin 8
#menu hidden
menu helpmsgrow 15
menu tabmsgrow 13

menu color border * #00000000 #00000000 none
menu color sel 0 #ffffffff #00000000 none
menu color title 0 #ff7ba3d0 #00000000 none
menu color tabmsg 0 #ff3a6496 #00000000 none
menu color unsel 0 #84b8ffff #00000000 none
menu color hotsel 0 #84b8ffff #00000000 none
menu color hotkey 0 #ffffffff #00000000 none
menu color help 0 #ffffffff #00000000 none
menu color scrollbar 0 #ffffffff #ff355594 none
menu color timeout 0 #ffffffff #00000000 none
menu color timeout_msg 0 #ffffffff #00000000 none
menu color cmdmark 0 #84b8ffff #00000000 none
menu color cmdline 0 #ffffffff #00000000 none

menu tabmsg Press Tab for full configuration options on menu items.
menu separator
""" % args

    def __get_image_stanza(self, is_xen, isDracut, **args):
        if isDracut:
            args["rootlabel"] = "live:CDLABEL=%(fslabel)s" % args
        else:
            args["rootlabel"] = "CDLABEL=%(fslabel)s" % args

        if not is_xen:
            template = """label %(short)s
  menu label %(long)s
  kernel vmlinuz%(index)s
  append initrd=initrd%(index)s.img  %(liveargs)s %(extra)s
"""
        else:
            template = """label %(short)s
  menu label %(long)s
  kernel mboot.c32
  append xen%(index)s.gz --- vmlinuz%(index)s root=%(rootlabel)s rootfstype=%(isofstype)s %(liveargs)s %(extra)s --- initrd%(index)s.img
"""
        if args.get("help"):
            template += """  text help
      %(help)s
  endtext
"""
        return template % args

    def __get_image_stanzas(self, isodir):
        versions = []
        kernels = self._get_kernel_versions()
        for kernel in kernels:
            for version in kernels[kernel]:
                versions.append(version)

        kernel_options = self._get_kernel_options()

        checkisomd5 = self._has_checkisomd5()

        # Stanzas for insertion into the config template
        linux = []
        basic = []
        check = []

        index = "0"
        for version in versions:
            (is_xen, isDracut) = self.__copy_kernel_and_initramfs(isodir, version, index)
            if index == "0":
                self._isDracut = isDracut

            default = self.__is_default_kernel(kernel, kernels)

            if default:
                long = self.product
            elif kernel.startswith("kernel-"):
                long = "%s (%s)" % (self.product, kernel[7:])
            else:
                long = "%s (%s)" % (self.product, kernel)

            # tell dracut not to ask for LUKS passwords or activate mdraid sets
            if isDracut:
                kern_opts = kernel_options + " rd.luks=0 rd.md=0 rd.dm=0"
            else:
                kern_opts = kernel_options

            linux.append(self.__get_image_stanza(is_xen, isDracut,
                                           fslabel = self.fslabel,
                                           isofstype = "auto",
                                           liveargs = kern_opts,
                                           long = "^Start " + long,
                                           short = "linux" + index,
                                           extra = "",
                                           help = "",
                                           index = index))

            if default:
                linux[-1] += "  menu default\n"

            basic.append(self.__get_image_stanza(is_xen, isDracut,
                                           fslabel = self.fslabel,
                                           isofstype = "auto",
                                           liveargs = kern_opts,
                                           long = "Start " + long + " in ^basic graphics mode.",
                                           short = "basic" + index,
                                           extra = "xdriver=vesa nomodeset",
                                           help = "Try this option out if you're having trouble starting.",
                                           index = index))

            if checkisomd5:
                check.append(self.__get_image_stanza(is_xen, isDracut,
                                               fslabel = self.fslabel,
                                               isofstype = "auto",
                                               liveargs = kern_opts,
                                               long = "^Test this media & start " + long,
                                               short = "check" + index,
                                               extra = "rd.live.check",
                                               help = "",
                                               index = index))
            else:
                check.append(None)

            index = str(int(index) + 1)

        return (linux, basic, check)

    def __get_memtest_stanza(self, isodir):
        memtest = glob.glob(self._instroot + "/boot/memtest86*")
        if not memtest:
            return ""

        shutil.copyfile(memtest[0], isodir + "/isolinux/memtest")

        return """label memtest
  menu label Run a ^memory test.
  text help
    If your system is having issues, an problem with your 
    system's memory may be the cause. Use this utility to 
    see if the memory is working correctly.
  endtext
  kernel memtest
"""

    def __get_local_stanza(self, isodir):
        return """label local
  menu label Boot from ^local drive
  localboot 0xffff
"""

    def _configure_syslinux_bootloader(self, isodir):
        """configure the boot loader"""
        makedirs(isodir + "/isolinux")

        menu = self.__find_syslinux_menu()

        self.__copy_syslinux_files(isodir, menu,
                                   self.__find_syslinux_mboot())

        background = ""
        if self.__copy_syslinux_background(isodir + "/isolinux/splash.jpg"):
            background = "splash.jpg"

        cfg = self.__get_basic_syslinux_config(menu = menu,
                                               background = background,
                                               title = self.title,
                                               timeout = self._timeout * 10)
        cfg += "menu separator\n"

        linux, basic, check = self.__get_image_stanzas(isodir)
        # Add linux stanzas to main menu
        for s in linux:
            cfg += s
        cfg += "menu separator\n"

        cfg += """menu begin ^Troubleshooting
  menu title Troubleshooting
"""
        # Add basic video and check to submenu
        for b, c in zip(basic, check):
            cfg += b
            if c:
                cfg += c

        cfg += self.__get_memtest_stanza(isodir)
        cfg += "menu separator\n"

        cfg += self.__get_local_stanza(isodir)
        cfg += self._get_isolinux_stanzas(isodir)

        cfg += """menu separator
label returntomain
  menu label Return to ^main menu.
  menu exit
menu end
"""
        cfgf = open(isodir + "/isolinux/isolinux.cfg", "w")
        cfgf.write(cfg)
        cfgf.close()

    def __copy_efi_files(self, isodir):
        if not os.path.exists(self._instroot + "/boot/efi/EFI/redhat/grub.efi"):
            return False
        shutil.copy(self._instroot + "/boot/efi/EFI/redhat/grub.efi",
                    isodir + "/EFI/boot/grub.efi")

        # Should exist, but if it doesn't we should fail
        if os.path.exists(self._instroot + "/boot/grub/splash.xpm.gz"):
            shutil.copy(self._instroot + "/boot/grub/splash.xpm.gz",
                        isodir + "/EFI/boot/splash.xpm.gz")

        return True

    def __get_basic_efi_config(self, **args):
        return """
default=0
splashimage=/EFI/boot/splash.xpm.gz
timeout %(timeout)d
hiddenmenu

""" %args

    def __get_efi_image_stanza(self, **args):
        if self._isDracut:
            args["rootlabel"] = "live:LABEL=%(fslabel)s" % args
        else:
            args["rootlabel"] = "CDLABEL=%(fslabel)s" % args
        return """title %(long)s
  kernel /EFI/boot/vmlinuz%(index)s root=%(rootlabel)s rootfstype=%(isofstype)s %(liveargs)s %(extra)s
  initrd /EFI/boot/initrd%(index)s.img
""" %args

    def __get_efi_image_stanzas(self, isodir, name):
        # FIXME: this only supports one kernel right now...

        kernel_options = self._get_kernel_options()
        checkisomd5 = self._has_checkisomd5()

        cfg = ""

        for index in range(0, 9):
            # we don't support xen kernels
            if os.path.exists("%s/EFI/boot/xen%d.gz" %(isodir, index)):
                continue
            cfg += self.__get_efi_image_stanza(fslabel = self.fslabel,
                                               isofstype = "auto",
                                               liveargs = kernel_options,
                                               long = name,
                                               extra = "", index = index)
            if checkisomd5:
                cfg += self.__get_efi_image_stanza(fslabel = self.fslabel,
                                                   isofstype = "auto",
                                                   liveargs = kernel_options,
                                                   long = "Verify and Boot " + name,
                                                   extra = "rd.live.check",
                                                   index = index)
            break

        return cfg

    def _configure_efi_bootloader(self, isodir):
        """Set up the configuration for an EFI bootloader"""
        makedirs(isodir + "/EFI/boot")

        if not self.__copy_efi_files(isodir):
            shutil.rmtree(isodir + "/EFI")
            return

        for f in os.listdir(isodir + "/isolinux"):
            os.link("%s/isolinux/%s" %(isodir, f),
                    "%s/EFI/boot/%s" %(isodir, f))


        cfg = self.__get_basic_efi_config(name = self.name,
                                          timeout = self._timeout)
        cfg += self.__get_efi_image_stanzas(isodir, self.name)

        cfgf = open(isodir + "/EFI/boot/grub.conf", "w")
        cfgf.write(cfg)
        cfgf.close()

        # first gen mactel machines get the bootloader name wrong apparently
        if rpmUtils.arch.getBaseArch() == "i386":
            os.link(isodir + "/EFI/boot/grub.efi", isodir + "/EFI/boot/boot.efi")
            os.link(isodir + "/EFI/boot/grub.conf", isodir + "/EFI/boot/boot.conf")

        # for most things, we want them named boot$efiarch
        efiarch = {"i386": "ia32", "x86_64": "x64"}
        efiname = efiarch[rpmUtils.arch.getBaseArch()]
        os.rename(isodir + "/EFI/boot/grub.efi", isodir + "/EFI/boot/boot%s.efi" %(efiname,))
        os.link(isodir + "/EFI/boot/grub.conf", isodir + "/EFI/boot/boot%s.conf" %(efiname,))


    def _configure_bootloader(self, isodir):
        self._configure_syslinux_bootloader(isodir)
        self._configure_efi_bootloader(isodir)

class mipsDebLiveImageCreator(DebLiveImageCreatorBase):

    def _get_required_packages(self):
        return []

    def _get_excluded_packages(self):
        # kind of hacky, but exclude memtest86+ on ppc so it can stay in cfg
        return ["memtest86+"] + \
               LiveImageCreatorBase._get_excluded_packages(self)


    def __copy_kernel_and_initramfs(self, destdir, version, index):
        bootdir = self._instroot + "/boot"

        makedirs(destdir)

        if os.path.exists(bootdir + "/vmlinuz-" + version):
            shutil.copyfile(bootdir + "/vmlinuz-" + version,
                            destdir + "/vmlinuz%(index)s")
        elif os.path.exists(bootdir + "/vmlinux-" + version):
            shutil.copyfile(bootdir + "/vmlinux-" + version,
                            destdir + "/vmlinuz%(index)s")

	#gen initrd
        initrd_cmd = ["/usr/sbin/update-initramfs","-c","-t","-k","%(version)s"]
        subprocess.call(initrd_cmd, preexec_fn = self._chroot)

        if os.path.exists(bootdir + "initrd.img" + version):
            shutil.copyfile(bootdir + "initrd.img" + version,
                            destdir + "initrd%(index)s.img"
        

    def __get_basic_pmon_config(self, **args):
        return """
timeout=%(timeout)d
default 0
showmenu 1
""" % args

    def __get_image_stanza(self, **args):
        return """

title  %(long)s
  kernel /dev/fs/ext2@usb0/boot/vmlinuz%(index)s
  initrd /dev/fs/ext2@usb0/boot/initrd%(index)s.img
  args console=tty  %(liveargs)s %(extra)s"
""" % args


    def __write_pmon_config(self, isodir):
        cfg = self.__get_basic_pmon_config(name = self.name,
                                             timeout = self._timeout * 100)

        kernel_options = self._get_kernel_options()

        versions = self._get_kernel_versions().values()

        index = "0"
        for version in versions:
            self.__copy_kernel_and_initramfs(isodir + "/boot/" , version, index)

            cfg += self.__get_image_stanza(fslabel = self.fslabel,
                                           isofstype = "auto",
                                           short = "linux",
                                           long = "Kernel %(version)s",
                                           extra = "",
                                           liveargs = kernel_options,
                                           index = index)

            index = str(int(index) + 1)

        f = open(isodir + "/boot"  + "/boot.cfg", "w")
        f.write(cfg)
        f.close()


    def _configure_bootloader(self, isodir):
        """configure the boot loader"""

        self.__write_pmon_config(isodir)

    def _create_bootconfig(self):
        """Configure the image so that it's bootable."""
        self._configure_bootloader(self.__ensure_isodir())

    def __ensure_isodir(self):
        if self.__isodir is None:
            self.__isodir = self._mkdtemp("iso-")
        return self.__isodir

    def __create_img(self, isodir):
        liveimgdir = self._mkdtemp()
        liveimg = ExtDiskMount(SparseLoopbackDisk(liveimgdir + "/" + self.name + ".img",
                                                  self.__image_size),
                               self._instroot,
                               self.__fstype,
                               self.__blocksize,
                               self.fslabel,
                               self.tmpdir)
    def _mount_instroot(self, base_on = None):
#        pass
        self.base_on = True
        LoopImageCreator._mount_instroot(self, base_on)
        self.__mount_isoroot()

    def __mount_isoroot( self ):
        liveimg = self._outdir + "/" + self.name + ".img"
        self.__liveloop = ExtDiskMount(SparseLoopbackDisk(liveimg,
                                                  4096L * 1024 * 1024),
                               self.__ensure_isodir(),
                               "ext3",
                               4096,
                               self.fslabel,
                               self.tmpdir)
        try:
            self.__liveloop.mount()
        except MountError, e:
            raise CreatorError("Failed to loopback mount '%s' : %s" %
                               (liveimg, e))


    def _unmount_instroot(self):
#        pass
        LoopImageCreator._unmount_instroot(self)
        self.__unmount_isoroot()

    def __unmount_isoroot(self):
        if not self.__liveloop is None:
            self.__liveloop.cleanup()

    def _stage_final_image(self):
        try:
            makedirs(self.__ensure_isodir() + "/live")

            self._resparse()

            if not self.skip_minimize:
                pass

            if self.skip_compression:
                shutil.move(self._image, self.__isodir + "/live/filesystem.ext3")
                if os.stat(self.__isodir + "/live/filesystem.ext3").st_size >= 4*1024*1024*1024:
                    self._isofstype = "udf"
                    logging.warn("Switching to UDF due to size of live/filesystem.ext3")
            else:
                instloop = DiskMount( LoopbackDisk(self._image,0), self._instroot)
                instloop.mount()
                mksquashfs(self._instroot,
                           self.__isodir + "/live/filesystem.squashfs",
                           self.compress_type)
                if os.stat(self.__isodir + "/live/filesystem.squashfs").st_size >= 4*1024*1024*1024:
                    self._isofstype = "udf"
                    logging.warn("Switching to UDF due to size of live/filesystem.squashfs")
                instloop.cleanup()
            

        finally:
            shutil.rmtree(self.__isodir, ignore_errors = True)
            self.__isodir = None



def LiveImageCreator(arch):
    if arch in ("i386", "amd64"):
        Creator = x86DebLiveImageCreator
    elif arch in ("mipsel",):
        Creator = mipsDebLiveImageCreator

    else:
        raise CreatorError("Architecture not supported!")
    
    return Creator
