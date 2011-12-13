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
import errno
import subprocess

import pykickstart.parser

#from imgcreate.errors import *

def makedirs(dirname):
    """A version of os.makedirs() that doesn't throw an
    exception if the leaf directory already exists.
    """
    try:
        os.makedirs(dirname)
    except OSError, e:
        if e.errno != errno.EEXIST:
            raise


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
    def __init__(self, rootdir, opts=None):
        self.rootdir = rootdir
        self.repos = []
        self.opts = opts

    def addRepo(self, fullurl):
        self.repos.append("deb " + fullurl)

    def _writeSourcesList(self):
        makedirs("%s/etc/apt/" % self.rootdir)
        sourcesListPath = self.rootdir + "/etc/apt/sources.list"
        sourcesList = "\n".join(self.repos)

        f = file(sourcesListPath, "w+")
        f.write(sourcesList)
        f.close()

        os.chmod(sourcesListPath, 0644)

    def setup(self):
        makedirs( self.rootdir + '/var/log/')
        self.logfile = open( self.rootdir + '/var/log/bootstrap.log', 'w')
        self._writeSourcesList()
        self.repocache = apt.Cache( None, self.rootdir )
        self.repocache.update()
        self.repocache.open()

    def depends(self, pkglist):
        for name in pkglist:
            before_num = len(pkglist)
            self.addDepends( pkglist, self.repocache[name].candidate.dependencies)
            after_num = len(pkglist)
            if before_num != after_num:
                self.depends( pkglist )

    def addDepends(self, pkglist, deps):
        for dep in deps:
            for basedep in dep.or_dependencies:
                if self.repocache.has_key(basedep.name):
                    if basedep.name not in pkglist:
                        pkglist.append( basedep.name )
                    break 

    def findPackages(self):
        self.requiredpkg = []
        self.basepkg = []
        self.pkglist = []
        for i in self.repocache:
            pkg = i.candidate
            if pkg.priority == "required":
                self.requiredpkg.append( pkg.package.name ) 
#                self.pkglist.append( pkg.package.name )
            if pkg.priority == "important":
                self.basepkg.append( pkg.package.name )
#                self.pkglist.append( pkg.package.name )
        self.depends( self.requiredpkg )
        self.depends( self.basepkg )
        ubase = [ a for a in self.basepkg if a not in self.requiredpkg]
        self.basepkg = ubase
        return (self.requiredpkg, self.basepkg, self.requiredpkg + self.basepkg)

    def downloadPackages(self, pkglist):
        for k in pkglist:
            for i in range( 1, 5 ):
                try:
                    self.repocache[k].candidate.fetch_binary( self.rootdir + '/var/cache/apt/archives/')
                except apt.package.FetchError,e :
                    if i > 5:
                        print "Can't donwload %s in 5 times, please check network" % k
                        raise apt.package.FetchError
                    else:
                        continue
                else:
                    break

    def _debExtract(self, reqpkg):
        for p in reqpkg:
            os.system('dpkg -x %s %s'%( self.getDebPath(self.rootdir, p), self.rootdir) )

    def _setup_devices( self, rootdir ):
        pass

    def preInstall(self, req):
        dpkgdir = self.rootdir + '/var/lib/dpkg/'
        dpkgrecord = self.repocache['dpkg'].candidate.record

        self._debExtract( req )

        makedirs('%sinfo' % dpkgdir)

        self._setup_devices( self.rootdir )
        
        f = open(dpkgdir+'status','w')
        c = "Package: %s\n" % dpkgrecord['Package']
        c += "Version: %s\n" % dpkgrecord['Version']
        c += "Status: install ok installed\n"
        f.write(c)
        f.close()

        f = open(dpkgdir+'available','w')
        f.close()

        f = open(dpkgdir+'info/%s.list' % dpkgrecord['Package'],'w')
        f.close()

        self._setup_proc( self.rootdir )
        subprocess.call(["/sbin/ldconfig"], preexec_fn = self._chroot)
        # ln mawk
        os.symlink('mawk',"%s/usr/bin/awk"%self.rootdir)
        
    def _chroot(self):
        """Chroot into the install root.

        This method may be used by subclasses when executing programs inside
        the install root e.g.

          subprocess.call(["/bin/ls"], preexec_fn = self.chroot)

        """
        os.chroot(self.rootdir)
        os.chdir("/")

    def chrootCall(self, cmd, stdin=None):
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'
        env['DEBCONF_NONINTERACTIVE_SEEN'] = 'true'
        env['LANG'] = 'C'
        pipe = subprocess.Popen( cmd, env=env, stdin = subprocess.PIPE, stderr = self.logfile, preexec_fn = self._chroot )
        if stdin:
            pipe.stdin.write(stdin + '\n')
        
        pipe.wait()
    
    def _setup_proc ( self, rootdir ):
        pass

    def getDebPath( self, rootdir='', pkgname='' ):
        debfile = self.repocache[ pkgname ].candidate.filename.split('/')[-1]
        filepath = "%s%s/%s" % ( rootdir, '/var/cache/apt/archives/', debfile )
        return filepath

    def installCore(self, pkglist):
       
        for p in pkglist:
            if p == 'mawk':
                os.remove("%s/usr/bin/awk"%self.rootdir)
            debfile = self.getDebPath( '', p )
            cmd = ('dpkg','--force-depends', '--install','%s' % debfile)
            self.chrootCall(cmd, 'y')
            if p == 'dpkg':
                 if not os.path.exists('%s/etc/localtime' % self.rootdir):
                     os.symlink('/usr/share/zoneinfo/UTC','%s/etc/localtime' % self.rootdir )
        

    def installBase(self, pkglist):

        debfiles = [self.getDebPath( '', p ) for p in pkglist ]
        cmd = ['dpkg', '--force-overwrite', '--force-confold', '--skip-same-version', '--unpack']
        cmd.extend(debfiles)
        self.chrootCall( cmd, 'y')

        cmd = ('dpkg', '--force-confold', '--skip-same-version', '--configure', '-a')
        self.chrootCall( cmd, 'y')
        os.rename('%s/sbin/start-stop-daemon.REAL' % self.rootdir, '%s/sbin/start-stop-daemon' % self.rootdir )

    def cleanup(self):
        cmd = ('apt-get','clean')
        self.chrootCall( cmd )
        self.logfile.close()

    def installRequired(self, req):
        # install
        debfiles = [self.getDebPath( '', p ) for p in req ]
        cmd = ['dpkg', '--force-depends', '--unpack']
        cmd.extend(debfiles)
        self.chrootCall( cmd, 'y')

        # config
        os.rename('%s/sbin/start-stop-daemon' % self.rootdir, '%s/sbin/start-stop-daemon.REAL' % self.rootdir )
        f = open('%s/sbin/start-stop-daemon' % self.rootdir, 'w')
        c = '#!/bin/sh\n'
        c += 'echo\n'
        c += 'echo \"Warning: Fake start-stop-daemon called, doing nothing\"'
        f.write(c)
        f.close()
        os.chmod( '%s/sbin/start-stop-daemon' % self.rootdir, 0755 )

        f = open('%s/var/lib/dpkg/cmethopt'% self.rootdir, 'w')
        f.write('apt apt')
        f.close()
        os.chmod('%s/var/lib/dpkg/cmethopt'% self.rootdir, 0644)

        cmd = ('dpkg',  '--configure', '--pending', '--force-configure-any', '--force-depends')
        self.chrootCall( cmd, 'y' )

    def debootstrap(self):
        ( req, base, alls ) = self.findPackages()
        self.downloadPackages( alls )
        self.preInstall( req )
        c = [ 'base-passwd','base-files', 'dpkg', 'libc6', 'perl-base',  'mawk', 'debconf' ]
        self.installCore(c)
        self.installRequired( req )
        self.installBase(base)

    def installExtraPackage( self, pkgs):
        cmd = ['apt-get', '-y', '--force-yes', 'install']
        cmd = cmd + pkgs
        self.chrootCall ( cmd )


class Apt(object):
    def __init__(self, releasever=None):
        """
        releasever = optional value to use in replacing $releasever in repos
        """
        self.releasever = releasever
        self.extrapkgs = []

    def doFileLogSetup(self, uid, logfile):
        # don't do the file log for the livecd as it can lead to open fds
        # being left and an inability to clean up after ourself
        pass

    def close(self):
        pass

    def __del__(self):
        pass

    def _writeConf(self, arch):
        if arch:
            makedirs( self.rootdir + "/etc/apt/")
            confpath = self.rootdir + '/etc/apt/apt.conf'
            conf  = "APT\n"
            conf += "{\n"
            conf += "Architecture \"%s\";\n" % arch
            conf += "};\n"

            f = file(confpath, "w+")
            f.write(conf)
            f.close()

            os.chmod(confpath, 0644)


    def setup(self, installroot, arch=None):
        self.rootdir=installroot
        self._writeConf(arch)
        self.installer = Debootstrap(installroot)

    def selectPackage(self, pkg):
        """Select a given package.  Can be specified with name.arch or name*"""
        self.extrapkgs.append(pkg)
        
    def deselectPackage(self, pkg):
        """Deselect package.  Can be specified as name.arch or name*"""
        self.extrapkgs.remove(pkg)


    def selectGroup(self, grp, include = pykickstart.parser.GROUP_DEFAULT):
        pass

    def addRepository(self, fullurl):
        self.installer.addRepo(fullurl)
            
    def runInstall(self):
        os.environ["HOME"] = "/"
        self.installer.setup()
        self.installer.debootstrap()
        if len(self.extrapkgs):
            self.installer.installExtraPackage( self.extrapkgs )
        self.installer.cleanup()
