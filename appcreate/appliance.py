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

class ApplianceImageCreator(ImageCreator):
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
        
        #additional modules to include   
        self.modules = ["sym53c8xx", "aic7xxx", "mptspi"]
        self.modules.extend(kickstart.get_modules(self.ks))
        

    def _get_fstab(self):
        s = ""
        for mp in self.__instloop.mountOrder:
            p = None
            for p1 in self.__instloop.partitions:
                if p1['mountpoint'] == mp:
                    p = p1
                    break

            s +=  "%(device)s  %(mountpoint)s %(fstype)s    defaults,noatime 0 0\n" %  {
                'device': "/dev/%s%-d" % (p['disk'], p['num']),
                'mountpoint': p['mountpoint'],
                'fstype': p['fstype'] }

        s += "devpts     /dev/pts  devpts  gid=5,mode=620   0 0\n"
        s += "tmpfs      /dev/shm  tmpfs   defaults         0 0\n"
        s += "proc       /proc     proc    defaults         0 0\n"
        s += "sysfs      /sys      sysfs   defaults         0 0\n"
        return s
    
    
    def _create_mkinitrd_config(self):
        #write  to tell which modules to be included in initrd
        
        extramods = ""
        for module in self.modules:
            extramods += '%s ' % module
            
        mkinitrd = ""
        mkinitrd += "PROBE=\"no\"\n"
        mkinitrd += "MODULES=\"ext3 ata_piix sd_mod libata scsi_mod\"\n"
        mkinitrd += "MODULES=\"%s\"\n" % extramods
        mkinitrd += "rootfs=\"ext3\"\n"
        mkinitrd += "rootopts=\"defaults\"\n"
        
        logging.debug("Writing mkinitrd config %s/etc/sysconfig/mkinitrd" % self._instroot)
        os.makedirs(self._instroot + "/etc/sysconfig/",mode=644)
        cfg = open(self._instroot + "/etc/sysconfig/mkinitrd", "w")
        cfg.write(mkinitrd)
        cfg.close()
                       
    
    #
    # Actual implementation
    #
    def _mount_instroot(self, base_on = None):
        self.__imgdir = self._mkdtemp()
        
        #list of partitions from kickstart file
        parts = kickstart.get_partitions(self.ks)
        
        #list of disks where a disk is an dict with name: and size
        disks = []

        for i in range(len(parts)):
            if parts[i].disk:
                disk = parts[i].disk
            else:
                raise CreatorError("Failed to create disks, no --ondisk specified in partition line of ks file")
                    
            size =   parts[i].size * 1024L * 1024L
            
            if len(disks) == 0:
                disks.append({ 'name': disk, 'size': size })
            else:
                found = 'false' 
                for j in range(len(disks)):
                    if disks[j]['name'] == disk:
                        disks[j]['size'] = disks[j]['size'] + size
                        found = 'true'
                        break
                    else: found = 'false'
                if found == 'false':
                    disks.append({ 'name': disk, 'size': size })    
            
                        
        #create disk
        for item in disks:
            logging.debug("Adding disk %s as %s/%s-%s.raws" % (item['name'], self.__imgdir,self.name, item['name']))
            disk = SparseLoopbackDisk("%s/%s-%s.raw" % (self.__imgdir,self.name, item['name']),item['size'])
            self.__disks[item['name']] = disk

        self.__instloop = PartitionedMount(self.__disks,
                                           self._instroot)

        for p in parts:
            self.__instloop.add_partition(int(p.size), p.disk, p.mountpoint, p.fstype)

        try:
            self.__instloop.mount()
        except MountError, e:
            raise CreatorError("Failed mount disks : %s" % e)
        
        self._create_mkinitrd_config()


    def _get_required_packages(self):
        return ["grub"]

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
        cfg = open(self._instroot + "/boot/grub/device.map", "w")
        cfg.write(devmap)
        cfg.close()

    def _get_grub_boot_config(self):
        bootdevnum = None
        rootdevnum = None
        rootdev = None
        for p in self.__instloop.partitions:
            if p['mountpoint'] == "/boot":
                bootdevnum = p['num'] - 1
            elif p['mountpoint'] == "/" and bootdevnum is None:
                bootdevnum = p['num'] - 1

            if p['mountpoint'] == "/":
                rootdevnum = p['num'] - 1
                rootdev = "/dev/%s%-d" % (p['disk'], p['num'])

        prefix = ""
        if bootdevnum == rootdevnum:
            prefix = "/boot"

        return (bootdevnum, rootdevnum, rootdev, prefix)

    def _create_grub_config(self):
        (bootdevnum, rootdevnum, rootdev, prefix) = self._get_grub_boot_config()
        options = self.ks.handler.bootloader.appendLine

        # NB we're assuming that grub config is on the first physical disk
        # ie /boot must be on sda, or if there's no /boot, then / must be sda

        # XXX don't hardcode default kernel - see livecd code
        grub = ""
        grub += "default=0\n"
        grub += "timeout=5\n"
        grub += "splashimage=(hd0,%d)%s/grub/splash.xpm.gz\n" % (bootdevnum, prefix)
        grub += "hiddenmenu\n"
        
        versions = []
        kernels = self._get_kernel_versions()
        for kernel in kernels:
            for version in kernels[kernel]:
                versions.append(version)

        for v in versions:
            grub += "title %s (%s)\n" % (self.name, v)
            grub += "        root (hd0,%d)\n" % bootdevnum
            grub += "        kernel %s/vmlinuz-%s ro root=%s %s\n" % (prefix, v, rootdev, options)
            grub += "        initrd %s/initrd-%s.img\n" % (prefix, v)

        logging.debug("Writing grub config %s/boot/grub/grub.conf" % self._instroot)
        cfg = open(self._instroot + "/boot/grub/grub.conf", "w")
        cfg.write(grub)
        cfg.close()

    def _copy_grub_files(self):
        imgpath = None
        for machine in ["x86_64-redhat", "i386-redhat"]:
            imgpath = self._instroot + "/usr/share/grub/" + machine
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

    def _install_grub(self):
        (bootdevnum, rootdevnum, rootdev, prefix) = self._get_grub_boot_config()

        # Ensure all data is flushed to disk before doing grub install
        subprocess.call(["sync"])

        stage2 = self._instroot + "/boot/grub/stage2"
        setup = ""
       
        i = 0
        for name in self.__disks.keys():
            loopdev = self.__disks[name].device
            setup += "device (hd%s) %s\n" % (i,loopdev)
            i =i+1
        setup += "root (hd0,%d)\n" % bootdevnum
        setup += "setup --stage2=%s --prefix=%s/grub  (hd0)\n" % (stage2, prefix)
        setup += "quit\n"

        logging.debug("Installing grub to %s" % loopdev)
        grub = subprocess.Popen(["grub", "--batch", "--no-floppy"],
                                stdin=subprocess.PIPE)
        grub.communicate(setup)
        rc = grub.wait()
        if rc != 0:
            raise MountError("Unable to install grub bootloader")

    def _create_bootconfig(self):
        self._create_grub_devices()
        self._create_grub_config()
        self._copy_grub_files()
        self._install_grub()

    def _unmount_instroot(self):
        if not self.__instloop is None:
            self.__instloop.cleanup()

    def _resparse(self, size = None):
        return self.__instloop.resparse(size)
    
    
    
    def package(self, destdir,package,include):
        """Prepares the created image for final delivery.
           Stage
           add includes
           package
        """
        self._stage_final_image()
        
        #add stuff
        if include and os.path.isdir(include):
            logging.debug("adding everything in %s to %s" % (include,self._outdir))
            files = glob.glob('%s/*' % include)
            for file in files:
                if os.path.isdir(file):
                    logging.debug("adding dir %s to %s" % (file,os.path.join(self._outdir,os.path.basename(file))))
                    shutil.copytree(file, os.path.join(self._outdir,os.path.basename(file)),symlinks=False)
                else:
                    logging.debug("adding %s to %s" % (file,self._outdir))
                    shutil.copy(file, self._outdir)
        elif include:
            logging.debug("adding %s to %s" % (include,self._outdir))
            shutil.copy(include, self._outdir)
        
        #package
        (pkg, comp) = os.path.splitext(package)
        if comp:
            comp=comp.lstrip(".")
        
        if pkg == "zip":
            dst = "%s/%s.zip" % (destdir, self.name)
            files = glob.glob('%s/*' % self._outdir)
            if comp == "64":
                logging.debug("creating %s with ZIP64 extensions" %  (dst))
                z = zipfile.ZipFile(dst, "w", compression=8, allowZip64="True")
            else:
                logging.debug("creating %s" %  (dst))
                z = zipfile.ZipFile(dst, "w", compression=8, allowZip64="False")    
            for file in files:
                if file != dst:
                    if os.path.isdir(file):
                        #because zip sucks we cannot just add a dir 
                         for root, dirs, dirfiles in os.walk(file):
                             for dirfile in dirfiles:
                                 arcfile=self.name+"/"+root[len(os.path.commonprefix((os.path.dirname(file), root)))+1:]+"/"+dirfile 
                                 filepath=os.path.join(root,dirfile)
                                 logging.debug("adding %s to %s" % (arcfile,dst))
                                 z.write(filepath,arcfile, compress_type=None)
                    else:
                        logging.debug("adding %s to %s" % (os.path.join(self.name,os.path.basename(file)),dst))
                        z.write(file, arcname=os.path.join(self.name,os.path.basename(file)), compress_type=None)
            z.close()
                     
        elif pkg == "tar":
            if comp:
                dst = "%s/%s.tar.%s" % (destdir, self.name, comp)
            else:
                dst = "%s/%s.tar" % (destdir, self.name)    
            files = glob.glob('%s/*' % self._outdir)
            logging.debug("creating %s" %  (dst))
            tar = tarfile.open(dst, "w|"+comp)
            for file in files:
                logging.debug("adding %s to %s" % (file,dst))
                tar.add(file, arcname=os.path.join(self.name,os.path.basename(file)))
            tar.close()

               
        else:
            dst = os.path.join(destdir, self.name)
            logging.debug("creating destination dir: " + dst)
            makedirs(dst)
            for f in os.listdir(self._outdir):
                logging.debug("moving %s to %s" % (os.path.join(self._outdir, f), os.path.join(dst, f)))
                shutil.move(os.path.join(self._outdir, f),os.path.join(dst, f))        
        print "Finished"
        
        
        
    def _stage_final_image(self):
        """Stage the final system image in _outdir.
           Convert disks
           write meta data
        """
        self._resparse()
        
        #if disk_format is not raw convert the disk and put in _outdir     
        if self.__disk_format != "raw":
            self._convert_image()
        #else move to _outdir    
        else:
            logging.debug("moving disks to stage location")
            for name in self.__disks.keys():  
                src = "%s/%s-%s.%s" % (self.__imgdir, self.name,name, self.__disk_format)
                dst = "%s/%s-%s.%s" % (self._outdir, self.name,name, self.__disk_format)
                logging.debug("moving %s to %s" % (src,dst))
                shutil.move(src,dst)
        #write meta data in stage dir
        self._write_image_xml()    

    
        
    def _convert_image(self):
        #convert disk format
        for name in self.__disks.keys():
            dst = "%s/%s-%s.%s" % (self._outdir, self.name,name, self.__disk_format)       
            logging.debug("converting %s image to %s" % (self.__disks[name].lofile, dst))
            rc = subprocess.call(["qemu-img", "convert",
                                   "-f", "raw", self.__disks[name].lofile,
                                   "-O", self.__disk_format,  dst])
            if rc == 0:
                logging.debug("convert successful")
            if rc != 0:
                raise CreatorError("Unable to convert disk to %s" % self.__disk_format)



    def _write_image_xml(self):
        xml = "<image>\n"

        name_attributes = ""
        if self.appliance_version:
            name_attributes += " version='%s'" % self.appliance_version
        if self.appliance_release:
            name_attributes += " release='%s'" % self.appliance_release
        xml += "  <name%s>%s</name>\n" % (name_attributes, self.name)
        xml += "  <domain>\n"
        # XXX don't hardcode - determine based on the kernel we installed for grub
        # baremetal vs xen
        xml += "    <boot type='hvm'>\n"
        xml += "      <guest>\n"
        xml += "        <arch>%s</arch>\n" % os.uname()[4]
        xml += "      </guest>\n"
        xml += "      <os>\n"
        xml += "        <loader dev='hd'/>\n"
        xml += "      </os>\n"

        i = 0
        for name in self.__disks.keys():
            xml += "      <drive disk='%s-%s.%s' target='hd%s'/>\n" % (self.name,name, self.__disk_format,chr(ord('a')+i))
            i = i + 1
            
        xml += "    </boot>\n"
        xml += "    <devices>\n"
        xml += "      <vcpu>%s</vcpu>\n" % self.vcpu 
        xml += "      <memory>%d</memory>\n" %(self.vmem * 1024)
        for network in self.ks.handler.network.network: 
            xml += "      <interface/>\n"
        xml += "      <graphics/>\n"
        xml += "    </devices>\n"
        xml += "  </domain>\n"
        xml += "  <storage>\n"

        if self.checksum is True:
            for name in self.__disks.keys():
                diskpath = "%s/%s-%s.%s" % (self._outdir,self.name,name, self.__disk_format)
                disk_size = os.path.getsize(diskpath)
                meter_ct = 0
                meter = progress.TextMeter()
                meter.start(size=disk_size, text="Generating disk signature for %s-%s.%s" % (self.name,name,self.__disk_format))
                xml += "    <disk file='%s-%s.%s' use='system' format='%s'>\n" % (self.name,name, self.__disk_format, self.__disk_format)

                try:
                    import hashlib
                    m1 = hashlib.sha1()
                    m2 = hashlib.sha256()
                except:
                    import sha
                    m1 = sha.new()
                    m2 = None
                f = open(diskpath,"r")
                while 1:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    m1.update(chunk)
                    if m2:
                       m2.update(chunk)
                    meter.update(meter_ct)
                    meter_ct = meter_ct + 65536

                sha1checksum = m1.hexdigest()
                xml +=  """      <checksum type='sha1'>%s</checksum>\n""" % sha1checksum

                if m2:
                    sha256checksum = m2.hexdigest()
                    xml += """      <checksum type='sha256'>%s</checksum>\n""" % sha256checksum
                xml += "    </disk>\n"
        else:
            for name in self.__disks.keys():
                xml += "    <disk file='%s-%s.%s' use='system' format='%s'/>\n" % (self.name,name, self.__disk_format, self.__disk_format)

        xml += "  </storage>\n"
        xml += "</image>\n"

        logging.debug("writing image XML to %s/%s.xml" %  (self._outdir, self.name))
        cfg = open("%s/%s.xml" % (self._outdir, self.name), "w")
        cfg.write(xml)
        cfg.close()
        #print "Wrote: %s.xml" % self.name
                



