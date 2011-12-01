lang en_US.UTF-8
keyboard us
timezone US/Eastern
part / --size 1024

repo --name=development --baseurl="ftp://210.51.172.252/mirror/debian squeeze main"
bootloader --append="boot=live config"


%packages
live-boot 
live-boot-initramfs-tools 
live-config
live-config-sysvinit
memtest86+
linux-image-2.6-686
%end
