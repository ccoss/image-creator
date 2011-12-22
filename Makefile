SUBDIRS = appcreate debianimage imgcreate

PYTHON=python
PACKAGE = $(shell basename `pwd`)
PYVER := $(shell $(PYTHON) -c 'import sys; print "%.3s" %(sys.version)')
PYSYSDIR := $(shell $(PYTHON) -c 'import sys; print sys.prefix')
PYLIBDIR = $(PYSYSDIR)/lib/python$(PYVER)
PKGDIR = $(PYLIBDIR)/site-packages/

_default:
	@echo "nothing to make.  try make install"

clean:
	@echo "nothing to make clean"

install:
	for d in $(SUBDIRS); do \
		for p in $$d/*; do \
			install -p -D  $$p $(DESTDIR)/$(PKGDIR)/$$p; \
		done \
	done
	for p in config/*; do \
		install -D -p  $$p  $(DESTDIR)/usr/share/$(PACKAGE)/$$p; \
	done 
	install -D tools/livecd-creator $(DESTDIR)/usr/sbin/livecd-creator
	install -D tools/appliance-creator $(DESTDIR)/usr/sbin/appliance-creator
	install -D tools/installer-creator $(DESTDIR)/usr/sbin/installer-creator
	
