#!/usr/bin/env python
# Author:		Jane Curry and Andrew Findlay
# Date:			7th July 2012
# Copyright:		Skills 1st Ltd
# Description:		Creates trouble tickets from Zenoss events based on parameters
#			found in $ZENHOME/etc/zentt.conf
#			zentt runs as a Zenoss daemon and is started and stopped as any other Zenoss daemon
#
# Updates:
#

# Perform initial imports.
from daemon import Daemon
import os, sys
import logging
# Zenoss imports


# Perform Zenoss specific imports.
import Globals
from Products.ZenUtils.ZenScriptBase import ZenScriptBase
from Products.ZenEvents.Exceptions import ZenEventNotFound
from transaction import commit
from Products.ZenUtils import Time
from MySQLdb import OperationalError
import time, socket, re, subprocess, shlex, ConfigParser, datetime

# Discover paths to files.
pidfile = os.path.join(os.environ['ZENHOME'], 'var/zentt-localhost.pid')
zenconfpath = os.path.join(os.environ['ZENHOME'], 'etc/zentt.conf')
logfile = os.path.join(os.environ['ZENHOME'], 'log/zentt.log')

# Configure logging.

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# Is this needed here - also inside daemon code

logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s zen.zentt: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        filename=logfile,
        filemode='a')
logging.info('zentt logfile is %s ' % (logfile))

# If running in foreground we print debug data
fg = 0

# Match a list of strings against the list of regexes or literal strings in a specific config section
# e.g. to find whether a device group is of interest
# If no options in the section match the supplied prefix, return the supplied default value
#
def configREMatch( config, s, prefix, list, default ):
    seen = 0
    # Compare against all '^<prefix>.*' strings in this config section
    # First build a pattern to match the options we are interested in
    optpattern = re.compile( prefix+'-(re-)?', re.IGNORECASE )
    for opt in config.options(s):
	# Ignore config options that do not have the prefix of interest
	m = optpattern.match(opt)
	if not m: continue
	# Note that we have seen at least one matching option
	seen = 1
	# if fg: print "opt is ", opt
	# Is this a regex match item? (name has -re)
	suffix = m.group(1)
	if suffix and (suffix.lower() == 're-'):
	    # if fg: print ("option %s is a regex: %s" % (opt, config.get(s, opt).rstrip()))
	    # The option value should be treated as a regex
	    pattern = re.compile( config.get(s, opt).rstrip(), re.IGNORECASE )
	    # Is there a match for that pattern in the supplied list?
	    for item in list:
		# if fg: print "Testing item ", item
		if pattern.search( item ):
		    return True;
	else:
	    # Is the exact option value in the supplied list?
	    for item in list:
		if config.get(s, opt).rstrip().lower() == item.lower():
		    return True;
    if seen:
	# We saw at least one matching option and none of their values matched the list
        return False
    else:
	# We did not see any matching options
        return default

# Get an integer option value from the config
#
def getIntOptValue( config, s, opt ):
    value = config.get(s, opt).rstrip()
    if not re.search( '^[-+]?[0-9]+$', value ):
	logging.error("Option %s in section %s has a non-integer value: %s" % (opt, s, value))
	return 0
    return int(value)

# Match an integer against a range specified by -min and -max options
# or against a list of acceptable values.
# If the section specifies a range and also a list of values, any
# values outside the range will not be matched.
#
def configIntMatch( config, s, prefix, value ):
    # First build a pattern to match the options we are interested in
    optpattern = re.compile( prefix+'-(.+)', re.IGNORECASE )
    # Assume the value is in range until proved otherwise
    inrange = 1
    # Assume the value is not in the list until proved otherwise
    inlist = 0
    # We have not yet seen a range spec
    seenrange = 0
    # We have not yet seen a list spec
    seenlist = 0

    # if fg: print "configIntMatch ", prefix, " ", value

    # Walk through ALL the options
    for opt in config.options(s):
	# Ignore config options that do not have the prefix of interest
	m = optpattern.match(opt)
	if not m: continue

	optvalue = getIntOptValue( config, s, opt )
	suffix = m.group(1)

	# if fg: print "considering ", opt, " ", optvalue

	# Is this a -min option?
	if suffix and (suffix.lower() == 'min'):
	    seenrange = 1
	    if value < optvalue:
	        inrange = 0
	    continue

	# Is this a -man option?
	if suffix and (suffix.lower() == 'max'):
	    seenrange = 1
	    if value > optvalue:
	        inrange = 0
	    continue

        # All other options are assumed to be list values
	seenlist = 1
	if value == optvalue:
	    inlist = 1

    # Right, now we need to sort out the result!
    # If we have a range spec then the value must comply
    if seenrange and not inrange: return 0
    # If we have a list spec then the value must comply
    if seenlist and not inlist: return 0
    # Default case
    return 1

# Filter code to select events that match in a section
def selectEvent( config, s, evt ):
    # Does the event have a group that this section is interested in?
    if not configREMatch( config, s, 'devicegroups', evt.DeviceGroups.split('|'), True ):
	if fg: print "devicegroup match fails"
	return 0

    # Does the event have a devicegroup that this section wants to avoid?
    if configREMatch( config, s, 'notdevicegroups', evt.DeviceGroups.split('|'), False ):
	if fg: print "notdevicegroup match fails"
	return 0

    # Does the event have a device that this section is interested in?
    if not configREMatch( config, s, 'device', [evt.device], True ):
	if fg: print "device match fails"
	return 0

    # Does the event have a device that this section wants to avoid?
    if configREMatch( config, s, 'notdevice', [evt.device], False ):
	if fg: print "notdevice match fails"
	return 0

    # Does the event have a device class that this section is interested in?
    if not configREMatch( config, s, 'deviceclass', [evt.DeviceClass], True ):
	if fg: print "deviceclass match fails"
	return 0

    # Does the event have a device class that this section wants to avoid?
    if configREMatch( config, s, 'notdeviceclass', [evt.DeviceClass], False ):
	if fg: print "notdeviceclass match fails"
	return 0

    # Check the production state
    if not configIntMatch( config, s, 'prodstate', evt.prodState ):
	if fg: print "prodstate match fails"
	return 0

    # Check the eventState
    if not configIntMatch( config, s, 'eventstate', evt.eventState ):
	if fg: print "eventstate match fails"
	return 0

    # Check the severity
    if not configIntMatch( config, s, 'severity', evt.severity ):
	if fg: print "severity match fails"
	return 0

    # Does the event have a summary that this section is interested in?
    if not configREMatch( config, s, 'summary', [evt.summary], True ):
	if fg: print "summary match fails"
	return 0

    # Does the event have a summary that this section wants to avoid?
    if configREMatch( config, s, 'notsummary', [evt.summary], False ):
	if fg: print "notsummary match fails"
	return 0

    # Does the event have a message that this section is interested in?
    if not configREMatch( config, s, 'message', [evt.message], True ):
	if fg: print "message match fails"
	return 0

    # Does the event have a message that this section wants to avoid?
    if configREMatch( config, s, 'notmessage', [evt.message], False ):
	if fg: print "notmessage match fails"
	return 0

    # Does the event have a component that this section is interested in?
    if not configREMatch( config, s, 'component', [evt.component], True ):
	if fg: print "component match fails"
	return 0

    # Does the event have a component that this section wants to avoid?
    if configREMatch( config, s, 'notcomponent', [evt.component], False ):
	if fg: print "notcomponent match fails"
	return 0

    # Does the event have a location that this section is interested in?
    if not configREMatch( config, s, 'location', [evt.Location], True ):
	if fg: print "location match fails"
	return 0

    # Does the event have a location that this section wants to avoid?
    if configREMatch( config, s, 'notlocation', [evt.Location], False ):
	if fg: print "notlocation match fails"
	return 0

    # Does the event have a systems organiser that this section is interested in?
    if not configREMatch( config, s, 'systems', evt.Systems.split('|'), True ):
	if fg: print "systems match fails"
	return 0

    # Does the event have a systems organiser that this section wants to avoid?
    if configREMatch( config, s, 'notsystems', evt.Systems.split('|'), False ):
	if fg: print "notsystems match fails"
	return 0

    # Does the event have an ipaddress that this section is interested in?
    if not configREMatch( config, s, 'ipaddress', [evt.ipAddress], True ):
	if fg: print "ipaddress match fails"
	return 0

    # Does the event have an ipaddress that this section wants to avoid?
    if configREMatch( config, s, 'notipaddress', [evt.ipAddress], False ):
	if fg: print "notipaddress match fails"
	return 0

    # We want this one!
    return 1


# Function to analyse an event and possibly create a troubleticket
def analyseEvent( config, dmd, evt ):
    if fg: print "analyseEvent"

    # Configure initial variables for ticket create routine.
    ticket = None
    p = None
    ticketerror = 0
    ntickets = 0

    # We are only interested in new events
    if evt.eventState != 0:
        return 0

    # Log a warning if a device does not belong to any groups.
    if not evt.DeviceGroups.replace('|',''):
	logging.warning("Device %s is not in a device group in event %s" % (evt.device, evt.evid))

    # Consider each section in the config file
    for s in config.sections():
	if ((s == 'DAEMONSTUFF') or (s == 'AUTOCLEAR')):
	    # Ignore the main daemon config section and the history matching section
	    # (DEFAULT is automatically skipped)
	    continue

	if fg: print "Section ", s

	# Compare event against filters - do we want it?
	if not selectEvent( config, s, evt ):
	    continue

	if fg: print "########## after filter - about to create ticket"

	# OK - we need to create a ticket, so grab the command-line template
	ttcommand = config.get("DAEMONSTUFF", "ttcommand")
	# Parse that into a list of args
	ttargs = shlex.split( ttcommand )

	# Prepare a dictionary with all the things we might want to substitute
	data = {}
	# Start by loading in all of the DAEMONSTUFF options
	for opt in config.options('DAEMONSTUFF'):
	    data['%'+opt+'%'] = config.get('DAEMONSTUFF', opt).rstrip()
	# Next load in the 'param-*' options from the current section
	# (this may override some existing values)
	optpattern = re.compile( 'param-', re.IGNORECASE )
	for opt in config.options(s):
	    # Ignore config options that do not have the prefix of interest
	    m = optpattern.match(opt)
	    if not m: continue
	    # Load the value
	    data['%'+opt+'%'] = config.get(s, opt).rstrip()
        # Now load in data from the event
	data['%evid%'] = str(evt.evid)
	data['%device%'] = str(evt.device)
	data['%component%'] = str(evt.component)
	data['%eventclass%'] = str(evt.eventClass)
	data['%eventkey%'] = str(evt.eventKey)
	data['%summary%'] = str(evt.summary)
	data['%message%'] = str(evt.message)
	data['%severity%'] = str(evt.severity)
	data['%eventstate%'] = str(evt.eventState)
	data['%eventclasskey%'] = str(evt.eventClassKey)
	data['%eventgroup%'] = str(evt.eventGroup).lstrip('|')
	data['%statechange%'] = str(evt.stateChange)
	data['%firsttime%'] = str(evt.firstTime)
	data['%lasttime%'] = str(evt.lastTime)
	data['%count%'] = str(evt.count)
	data['%prodstate%'] = str(evt.prodState)
	data['%suppid%'] = str(evt.suppid)
	data['%manager%'] = str(evt.manager)
	data['%agent%'] = str(evt.agent)
	data['%deviceclass%'] = str(evt.DeviceClass)
	data['%location%'] = str(evt.Location)
	data['%systems%'] = str(evt.Systems).lstrip('|')
	data['%devicegroups%'] = str(evt.DeviceGroups).lstrip('|')
	data['%ipaddress%'] = str(evt.ipAddress)
	data['%facility%'] = str(evt.facility)
	data['%priority%'] = str(evt.priority)
	data['%ntevid%'] = str(evt.ntevid)
	data['%ownerid%'] = str(evt.ownerid)
	data['%clearid%'] = str(evt.clearid)
	data['%devicepriority%'] = str(evt.DevicePriority)
	data['%eventclassmapping%'] = str(evt.eventClassMapping)

	# NOTE:
	# May need to convert times to some other format.
	# Here is a handy pattern for parsing them...
	# pattern = '%Y/%m/%d %H:%M:%S'
	# epoch = int(time.mktime(time.strptime(evt.lastTime.split('.')[0], pattern)))

	# Work through the argument list substituting where we can.
	#
	for index,arg in enumerate(ttargs):
	    # if fg: print "string: ", arg
            # we have a string in 'arg' which may contain %var% substitution keys
	    # First we must split that string into a list where each %var% is a separate item
	    keypattern = re.compile( r'(%[a-z0-9_-]+%)', re.IGNORECASE )
	    keylist = re.split( keypattern, arg )
	    # Now walk through the list doing the substitutions
	    for index2,arg2 in enumerate(keylist):
		# if fg: print "key: ", arg2
		if data.has_key(arg2.lower()):
		    keylist[index2] = data[arg2.lower()]
		# if fg: print "KEY: ", keylist[index2]
	    # Finally, join all that up again and put it back in the main arg list
	    ttargs[index] = ''.join( keylist )
	    # if fg: print "STRING: ", ttargs[index], "\n"

	    
	if fg: print ttargs

	try:
	    # Run the ticket create script (while passing necessary arguments to it).
	    p = subprocess.Popen(ttargs, stdout=subprocess.PIPE)

	    if fg: print "after popen"

	    if not p:
		if fg: print ("Unable to run ticket creation command %s" % (ttcommand))
		logging.error("Unable to run ticket creation command %s" % (ttcommand))
		ticketerror = 1
		continue

	    # Let the command run and collect its output
	    (stdoutdata, stderrdata) = p.communicate()
	    if fg: print "TT Script said: ", stdoutdata

	except OSError:
	    if fg: print ("Error while running ticket creation command")
	    logging.error("Error while running ticket creation command")
	    # Give up and try again later
	    ticketerror = 1
	    continue

	# Get the ticket ID
	ticket = stdoutdata.rstrip()
        # Sanity check
	if not re.search( r'[0-9]+', ticket ):
	    if fg: print ("No ticket ID returned from troubleticket system for event %s" % (evt.evid))
	    logging.error("No ticket ID returned from troubleticket system for event %s" % (evt.evid))
	    ticketerror = 1
	    continue

	ntickets += 1

	logging.info("Ticket %s created for event %s" % (ticket, evt.evid))

	if fg: print "########## ticket done"

	# If ticket was successfully created, acknowledge the event in Zenoss.
	eventlist = [evt.evid]
	if evt.eventState == 0:
	    try:
		# Ack
		dmd.ZenEventManager.manage_setEventStates(1, eventlist)

		# Update event info
		update="update status set summary='%s', ownerid='%s'" % (evt.summary + ' ticket created ' + str(ticket) , 'Ticket')
		whereClause = "where evid in ("
		whereClause += ",".join([ "'%s'" % evid for evid in eventlist]) + ")"
		reason = 'Trouble Ticket created'
		dmd.ZenEventManager.updateEvents(update, whereClause, reason)

	    # Ignore certain errors thrown by MySQL and Zenoss.
	    except OperationalError, err:
		if err[0] == 1205:
		    pass
		elif err[0] == 1213:
		    pass
		elif err[0] == 1422:
		    pass
		elif err[0] == 1206:
		    pass
		elif err[0] == 2002:
		    pass
		else:
		    raise
	    except ZenEventNotFound:
		pass

    if ticketerror: return -1

    # We did it!
    return ntickets


# Daemon code space begins here.
class MyDaemon(Daemon):
    def run(self):

        dmd = ZenScriptBase(connect=True).dmd

        logging.info(' Start of daemon run self')
        if fg: print ' Start of daemon run self'

        # Read in config file.
        config = ConfigParser.ConfigParser()
        config.read([zenconfpath])

        # Gather general config file options in to variables.
        ttcommand = config.get("DAEMONSTUFF", "ttcommand")
        cycletime = config.get("DAEMONSTUFF", "cycletime")

        # Configure logging within daemon code space.
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        logging.basicConfig(level=logging.INFO,
                format='%(asctime)s %(levelname)s zen.zentt: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
                filename=logfile,
                filemode='a')

        logging.info('Config: ttcommand is ' + ttcommand)
	logging.info('Config: cycletime is ' + cycletime)

        sys.stderr = open(logfile, 'a')

        # Run daemon forever.......
        while True:

	    if fg: print "\nzentt main loop\n"

	    # Keep track of how many tickets we have created
	    numttcreated = 0

            # Events to create new tickets for.....
            # Ticket creation cycle begins here.
            for e in dmd.ZenEventManager.getEventList([], "", "lastTime ASC, firstTime ASC"):

                # Define initial variables for ticket creation cycle.
                ttcreate = 0

		if fg: print "\n", e.evid

		# Get the event details
		evt = dmd.ZenEventManager.getEventDetailFromStatusOrHistory(e.evid)
		if not evt:
		    logging.warning("Event %s not found" % (e.evid))
		    continue

		# Create a ticket for all new events that match defined criteria
		tt = analyseEvent( config, dmd, evt)

		# Update the count of tickets created
		if tt > 0:
		    numttcreated = numttcreated + tt

		# No errors, but the event is new and no ticket was created for it
		if tt == 0:
		    # If no ticket was created then consider clearing the event
		    if fg: print "Checking AUTOCLEAR"

		    if selectEvent( config, 'AUTOCLEAR', evt ):
			if fg: print "Clearing event: ", e.evid

			try:
			    # Update event info
			    update="update status set summary='%s'" % (evt.summary + ' auto-cleared by zenTT ')
			    whereClause = "where evid = '%s'" % (e.evid)
			    reason = 'Event matches AUTOCLEAR criteria'

			    logging.info( "Clearing event %s: %s" % (e.evid, reason) )

			    dmd.ZenEventManager.updateEvents(update, whereClause, reason)

			    dmd.ZenEventManager.manage_deleteEvents(e.evid)

			# Ignore certain errors thrown by MySQL and Zenoss.
			except OperationalError, err:
			    if err[0] == 1205:
				pass
			    elif err[0] == 1213:
				pass
			    elif err[0] == 1422:
				pass
			    elif err[0] == 1206:
				pass
			    elif err[0] == 2002:
				pass
			    else:
				raise
			except ZenEventNotFound:
			    pass

            # Write activity summary to log file.
            if numttcreated > 0:
                    logging.info('Tickets created: %d', numttcreated)

            # Sleep for the amount of seconds configured in the cycletime setting before starting the next cycle.
            logging.debug(' End of cycle - time to sleep')
            time.sleep(int(cycletime))

# Daemon runtime options are defined here.
if __name__ == "__main__":
	daemon = MyDaemon(pidfile)

        # Grab the option.
	if len(sys.argv) == 2:

                # Option to start the daemon code in the foreground
		if 'fg' == sys.argv[1]:
                        if os.path.exists(zenconfpath):
                            logging.info('Starting zentt from zentt.py')
			    fg = 1
                            daemon.run()
                        else:
                            print '%s is missing, aborting fg.' % (zenconfpath)

                # Option to start the daemon.
		if 'start' == sys.argv[1]:
                        if os.path.exists(zenconfpath):
                            logging.info('Starting zentt from zentt.py')
                            daemon.start()
                        else:
                            print '%s is missing, aborting start.' % (zenconfpath)

                # Option to stop the daemon.
		elif 'stop' == sys.argv[1]:
                        if os.path.exists(pidfile):
                            logging.info('Deleting PID file %s ...', pidfile)
                            logging.info('zentt shutting down')
                        print 'stopping...'
			daemon.stop()

                # Option to restart the daemon.
		elif 'restart' == sys.argv[1]:
                        if os.path.exists(pidfile):
                            logging.info('Deleting PID file %s ...', pidfile)
                            logging.info('zentt shutting down')
                        print 'stopping...'
			daemon.stop()
                        if os.path.exists(zenconfpath):
                            logging.info('Starting zentt')
                            daemon.start()
                        else:
                            print '%s is missing, aborting start.' % (zenconfpath)

                # Option to get daemon status.
                elif 'status' == sys.argv[1]:
                        try:
                            pf = file(daemon.pidfile,'r')
                            pid = int(pf.read().strip())
                            pf.close()
                        except IOError:
                            pid = None

                        # Function to check for the existence of a unix pid.
                        def check_pid(pid):
                            try:
                                os.kill(pid, 0)
                            except OSError:
                                return False
                            else:
                                return True

                        if not pid:
                            print 'not running'
                        else:
                            if check_pid(pid) == True:
                                print 'program running; pid=%s' % (pid)
                            else:
                                print 'not running'

                # Option to generate XML options to be displayed when clicking on 
                # "edit config" in the Daemons section of the Zenoss UI.
                elif 'genxmlconfigs' == sys.argv[1]:
                        print "\
<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
  <configuration id=\"zentt\" >\n\
  <option id=\"\" type=\"string\" default=\"\" target=\"\" help=\"Edit%20the%20config%20\
file%20by%20navigating%20to%20view%20config%20-&gt;%20edit%20this%20\
configuration%20from%20the%20daemons%20page.\" />\n\
  <option id=\" \" type=\"string\" default=\"\" target=\"\" help=\"Do%20not%20\
click%20save%20on%20this%20page%20as%20it%20will%20clear%20the%20config%20file.\" />\n\
  <option id=\"  \" type=\"string\" default=\"\" target=\"\" help=\"If%20the%20\
config%20does%20get%20cleared%20it%20can%20be%20restored%20from%20zentt.conf.bak.\" />\n\
</configuration>"

		else:

                        # Print valid options if invalid option is specified.
			print "usage: zentt start|stop|restart|status|genxmlconfigs"
			sys.exit(2)
		sys.exit(0)

	else:

                # Print valid options if invalid option is specified.
		print "usage: zentt start|stop|restart|status|genxmlconfigs"
		sys.exit(2)
