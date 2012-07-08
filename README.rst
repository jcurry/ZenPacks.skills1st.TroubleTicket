=================================
ZenPacks.ZenSystems.TroubleTicket
=================================


Description
===========

Provides an interface between Zenoss and a Trouble Ticket system. In the sample test environment, the
Trouble Ticket system was Remedy 7.5 on a Windows 2008 R2 system.

The ZenPack assumes that the Trouble Ticket system will be on a separate system from
the Zenoss Server and that ssh will be used to communicate between the systems.  A polling daemon,
zentt, runs at a configurable interval (120 seconds by default) to assess any new Zenoss events
and create a ticket if filters are passed.  If a ticket is created then:
    * The Zenoss event is Acknowledged
    * The event ownerid field is set to Ticket
    * The event summary is modified to add the trouble ticket number returned by the ticket system
    * The event for the log is updated with the message "Trouble Ticket created"

In addition, the zentt daemon can close any Zenoss event for which a ticket has NOT been created and
which passes any specified filters.

The zentt daemon is highly configurable using its $ZENHOME/etc/zentt.conf file.  A sample is provided that will
be installed provided no existing $ZENHOME/etc/zentt.conf file exists.  See later section for zentt.conf details.

A sample shellscript, zenoss-remote-ticket, is shipped in the ZenPack's lib directory, to generate Remedy 
tickets.  This file must be copied to the Trouble Ticket system.  

zentt logs to $ZENHOME/log/zentt.log.
The sample zenoss-remote-ticket ticket creation script logs to tickets.log in the Cygwin home directory of the zenoss user.

The zentt.conf Configuration File
=================================

ZenTT reads its configuration file at startup.
The file has a structure similar to what you would find on Microsoft Windows INI files.
There are three sections with specific roles:

DEFAULT
    If present, the DEFAULT section supplies defaults for all the others.

DAEMONSTUFF
    This section provides overall daemon configuration. The main options are:

    * ttcommand: The command-line used to create a new trouble-ticket. See the *ttcommand* section below for details.
    * cycletime: The number of seconds to delay between polls.

AUTOCLEAR
    This section defines a filter that selects events to be automatically cleared if they have not triggered a ticket.
    Note that events are eligible to be cleared whatever their current eventState, so if you only want to clear new
    ones you should include eventstate in your filter definition.

Every other section in the file defines a filter for creating trouble-tickets.
One ticket is created for each section where the filter matches the event.

Sections may contain config options that are not part of the filter. Most of these will be ignored,
but any with names starting 'param-' will be available for substitution into the command-line when
a trouble-ticket is created. This allows tickets to be labelled in different ways depending on which
section triggers them.


Filtering
---------

ZenTT uses filters to select which events to process.
Filters can test for the presence or absence of particular values in almost every attribute of the event.
Each attribute can be tested against any number of values.
String-valued attributes can be tested for exact match or using regular expressions.
All string comparisons ignore case.
Numeric attributes can be also tested against minimum and maximum values.

Each test is one option in the *zentt.conf* configuration file.
Option names are not case sensitive but every option in a section must have a unique name,
so it is necessary to append a name or number to the basic option name e.g.:

::

  devicegroups-1: /Linux_group
  devicegroups-2: /Other
  devicegroups-server: /Server

Most tests have a 'not' form:

::

  notdevicegroups-1: /Unwanted

For a filter to accept an event, each 'normal' test must have at least one string that matches
and no strings in any 'not' test may match. If a particular test is not specified at all then it has no
effect.

To use a regular expression in a string test, append 're-' to the name:

::

  devicegroups-re-cust: ^/Cust/
  devicegroups-re-server: ^/Server/

This works for both the 'normal' and the 'not' forms.

Numeric tests can specify a list of acceptable values:

::

  prodstate-prod: 1000
  prodstate-preprod: 500
  prodstate-test: 400

They can also use the special 'min' and 'max' suffixes to specify a range:

::

  prodstate-min: 500
  prodstate-max: 2000

If both list and range are supplied, the range overrules the list.

Tests
-----

For a full description of the event attributes, see the TALES Expressions appendix in the Zenoss Admin Guide.

device, notdevice: string/regex
    The name of the device attached to the event. This is often the fully-qualified DNS name.

devicegroups, notdevicegroups: string/regex
    The group organiser assigned to the class

deviceclass, notdeviceclass: string/regex
    The device class

prodstate: number
    The production state of the device, expressed as a number.
    Zenoss typically uses 1000 for Production, 500 for Pre-production, 400 for Test, 300 for Maintenance, and -1 for Decommissioned.

eventstate: number
    0 = New, 1 = Acknowledged, 2 = Suppressed

severity: number
    0 = Clear, 1 = Debug, 2 = Info, 3 = Warning, 4 = Error, 5 = Critical

summary, notsummary: string/regex
    A text summary of the event

message, notmessage: string/regex
    Message body - may be the same as summary

component, notcomponent: string/regex
    The Zenoss daemon that reported the event

location, notlocation: string/regex
    The location organiser assigned to the event

systems, notsystems: string/regex
    The system organiser assigned to the device

ipaddress, notipaddress: string/regex
    The IPv4 address of the device

Filter Examples
---------------

Here is a filter that matches events from devices in the /Linux group and also from devices
in any /Server group except for /Server/Testing. Events must be at least Error (4) severity.

::

  devicegroups-1: /Linux
  devicegroups-re-2: ^/Server
  notdevicegroups-testservers: /Server/Testing
  severity-min: 4
                                                                 
Components
==========

The ZenPack has the following relevant files:
    * __init__.py to ensure that the example zentt.conf.example file is copied to $ZENHOME/etc when the ZenPack is installed. If no zentt.conf exists there then it will also be copied to $ZENHOME/etc/zentt.conf.
    * daemon.py is code to daemonise zentt.py
    * daemons/zentt also required to daemonise the zentt daemon. Has commented out strace debug line if you get desperate.
    * lib/zentt.conf.example with sample config file
    * lib/zenoss-remote-ticket with sample shellscript to be copied to Trouble Ticket system
    * zentt.py  This is the trouble ticket daemon code 


Requirements & Dependencies
===========================

    * Zenoss Versions Supported: 3.x NB. This will NOT work on 4.x
    * External Dependencies: ssh must be installed and tested between Zenoss and Trouble Ticket system.
    * ZenPack Dependencies: None
    * Installation Notes: zenhub and zopectl must be restart after installing this ZenPack and zentt must be started.
    * Configuration: 

Download
========
Download the appropriate package for your Zenoss version from the list
below.

* Zenoss 3.0+ `Latest Package for Python 2.6`_

Installation
============

Installing Cygwin OpenSsh on Windows
------------------------------------

Note that you can use any ssh server package that supports public key authentication. Here are instructions
for installing Cygwin OpenSsh on Windows 2008 R2. The sample password mypassword is used

    * Installed Cygwin using setup.exe from http://cygwin.com
    * Made available to all users
    * Base dir C:\cygwin
    * Packages stashed in C:\cygwin\downloads
    * Selected openssh in addition to the default packages
    * Right-click on Cygwin Terminal icon, Run as Administrator
    * In the terminal window:
        * ssh-host-config
        * Enable privilege separation
        * Allow it to create 'sshd' user
        * Allow it to install as a service
        * Leave the CYGWIN env variable blank
        * Accept the default name for the privileged account (cyg_server)
        * Allow it to create the account, use 'mypassword' as the password
        * net start sshd

    * The 'CYGWIN sshd' service is now running
    * Use Windows admin tool to create a user 'zenoss', password 'mypassword' password never expires.
    * Add the user to the 'Remote Desktop Users' group
    * Use Windows Firewall tool to allow inbound port 22 for SSH
    * In the cygwin terminal window, update passwd and group:
        * mkpasswd > /etc/passwd
        * mkgroup > /etc/group
    * Use rdesktop to login as zenoss, e.g.:
        * rdesktop -g 80% -w zenoss ec2-46-137-8-155.eu-west-1.compute.amazonaws.com
        * Start the Cygwin terminal
        * ssh-user-config
            * Create all the SSH2 key types but not the SSH1 type.
            * Set them all to allow login on this machine
    * Test SSH from another machine, (probably your Zenoss server) e.g.:
        * ssh zenoss@ec2-46-137-8-155.eu-west-1.compute.amazonaws.com
        * On your Zenoss sytem, as the zenoss user, check whether you have a .ssh directory with keys
        * If not, use 'ssh-keygen -t dsa' to create keys - leave the passphrase blank
        * Use scp to copy the .ssh/id_dsa.pub key to the Windows system, to the zenoss user's home directory:
            * cd ~/.ssh
            * scp id_dsa.pub zenoss@ec2-46-137-8-155.eu-west-1.compute.amazonaws.com:
            * You will need to provide the password
        * On the Windows system, append the id_dsa.pub file to the zenoss user's .ssh/authorized_keys file
            * cd .ssh
            * cat ../id_dsa.pub >> authorized keys
    * Test from the Zenoss Server as the zenoss user:
        * ssh  zenoss@ec2-46-137-8-155.eu-west-1.compute.amazonaws.com ls -la
        * You should not be prompted for a password and the command should run
        * NB. You MUST test the ssh connection otherwise the code will not be able to interpret the initial prompt for a host key and the daemon will fail.

Normal Installation (packaged egg)
----------------------------------
Copy the downloaded .egg to your Zenoss server and run the following commands as the zenoss
user::

   zenpack --install <package.egg>
   zenhub restart
   zopectl restart
   zentt start

Developer Installation (link mode)
----------------------------------
If you wish to further develop and possibly contribute back to this 
ZenPack you should clone the git repository, then install the ZenPack in
developer mode::

   zenpack --link --install <package>
   zenhub restart
   zopectl restart
   zentt start

Configuration
=============

Tested with Zenoss 3.1 against Remedy 7.5 on a Windows 2008 system

Change History
==============
* 1.0
   * Initial Release

Screenshots
===========
|myScreenshot|


.. External References Below. Nothing Below This Line Should Be Rendered

.. _Latest Package for Python 2.6: https://github.com/jcurry/ZenPacks.skills1st.TroubleTicket/blob/master/dist/ZenPacks.skills1st.TroubleTicket-1.0-py2.6.egg?raw=true

.. |myScreenshot| image:: http://github.com/jcurry/ZenPacks.skills1st.TroubleTicket/raw/master/screenshots/myScreenshot.jpg

                                                                        

