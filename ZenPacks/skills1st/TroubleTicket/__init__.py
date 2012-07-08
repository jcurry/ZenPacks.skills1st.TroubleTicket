import Globals
import os
import sys
from Products.ZenUtils.Utils import zenPath
from Products.ZenModel.ZenPack import ZenPack as ZenPackBase


skinsDir = os.path.join(os.path.dirname(__file__), 'skins')
from Products.CMFCore.DirectoryView import registerDirectory
if os.path.isdir(skinsDir):
    registerDirectory(skinsDir, globals())

class ZenPack(ZenPackBase):
#
# Code that is run when the ZenPack is installed
#
     def install(self, dmd):
         ZenPackBase.install(self, dmd)
         # Get Zenoss etc config directory
         etcDir = zenPath('etc')
         # Get example config file from lib directory of this ZenPack
         # Copy existing /etc/zentt.conf etc/zentt.conf.bak
         #   and install example conf file
         exConfFile = os.path.join(os.path.dirname(__file__), 'lib/zentt.conf.example')
         if os.path.exists(exConfFile):
             os.system('cp %s %s' % (exConfFile, zenPath('etc/zentt.conf.example') ) )
             if not os.path.exists(zenPath('etc/zentt.conf')):
                 os.system('cp %s %s' % (exConfFile, zenPath('etc/zentt.conf') ) )
                 print 'Copying %s to %s ' % (exConfFile, zenPath('etc/zentt.conf') )

# Code that is run when the ZenPack is removed

     def remove(self, dmd, leaveObjects=False):

         ZenPackBase.remove(self, dmd, leaveObjects=leaveObjects)

