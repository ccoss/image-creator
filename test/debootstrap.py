#! /usr/bin/python
import sys

from debianimage.aptinst import Debootstrap

def main():
    dbs=Debootstrap('/media/doc/koji/rootdir')
    dbs.addRepo('ftp://210.51.172.252/mirror/debian squeeze main')
    dbs.setup()
    dbs.debootstrap()
#    print "----" 
#    for a in dbs.requiredpkg.keys():
#        print a
#    print "===="
#    for a in dbs.basepkg.keys():
#        print a
#    for a in dbs.requiredpkg:
#         print a


if __name__ == "__main__":
    sys.exit(main())
