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

# First remove any default handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logger = logging.getLogger('ZenTT')
logger.setLevel(logging.DEBUG)
# create file handler
fh = logging.handlers.RotatingFileHandler( logfile, maxBytes=10000000, backupCount=3 )
fh.setLevel(logging.INFO)
# create formatter and add it to the handlers
logFormatter = logging.Formatter(fmt='%(asctime)s %(levelname)s ZenTT: %(message)s',
		                datefmt='%Y-%m-%d %H:%M:%S')

fh.setFormatter(logFormatter)
# add the handler to the logger
logger.addHandler(fh)

# Exception class for failure to create tickets
#
class TicketError(Exception):
    def __init__(self, errmsg):
        self.errmsg = errmsg

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
	# Is this a regex match item? (name has -re)
	suffix = m.group(1)
	if suffix and (suffix.lower() == 're-'):
	    # The option value should be treated as a regex
	    pattern = re.compile( config.get(s, opt).rstrip(), re.IGNORECASE )
	    # Is there a match for that pattern in the supplied list?
	    for item in list:
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
	logger.error("Option %s in section %s has a non-integer value: %s" % (opt, s, value))
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

    # Walk through ALL the options
    for opt in config.options(s):
	# Ignore config options that do not have the prefix of interest
	m = optpattern.match(opt)
	if not m: continue

	optvalue = getIntOptValue( config, s, opt )
	suffix = m.group(1)

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
	logger.debug( "devicegroup match fails" )
	return 0

    # Does the event have a devicegroup that this section wants to avoid?
    if configREMatch( config, s, 'notdevicegroups', evt.DeviceGroups.split('|'), False ):
	logger.debug( "notdevicegroup match fails" )
	return 0

    # Does the event have a device that this section is interested in?
    if not configREMatch( config, s, 'device', [evt.device], True ):
	logger.debug( "device match fails" )
	return 0

    # Does the event have a device that this section wants to avoid?
    if configREMatch( config, s, 'notdevice', [evt.device], False ):
	logger.debug( "notdevice match fails" )
	return 0

    # Does the event have a device class that this section is interested in?
    if not configREMatch( config, s, 'deviceclass', [evt.DeviceClass], True ):
	logger.debug( "deviceclass match fails" )
	return 0

    # Does the event have a device class that this section wants to avoid?
    if configREMatch( config, s, 'notdeviceclass', [evt.DeviceClass], False ):
	logger.debug( "notdeviceclass match fails" )
	return 0

    # Check the production state
    if not configIntMatch( config, s, 'prodstate', evt.prodState ):
	logger.debug( "prodstate match fails" )
	return 0

    # Check the eventState
    if not configIntMatch( config, s, 'eventstate', evt.eventState ):
	logger.debug( "eventstate match fails" )
	return 0

    # Check the severity
    if not configIntMatch( config, s, 'severity', evt.severity ):
	logger.debug( "severity match fails" )
	return 0

    # Does the event have a summary that this section is interested in?
    if not configREMatch( config, s, 'summary', [evt.summary], True ):
	logger.debug( "summary match fails" )
	return 0

    # Does the event have a summary that this section wants to avoid?
    if configREMatch( config, s, 'notsummary', [evt.summary], False ):
	logger.debug( "notsummary match fails" )
	return 0

    # Does the event have a message that this section is interested in?
    if not configREMatch( config, s, 'message', [evt.message], True ):
	logger.debug( "message match fails" )
	return 0

    # Does the event have a message that this section wants to avoid?
    if configREMatch( config, s, 'notmessage', [evt.message], False ):
	logger.debug( "notmessage match fails" )
	return 0

    # Does the event have a component that this section is interested in?
    if not configREMatch( config, s, 'component', [evt.component], True ):
	logger.debug( "component match fails" )
	return 0

    # Does the event have a component that this section wants to avoid?
    if configREMatch( config, s, 'notcomponent', [evt.component], False ):
	logger.debug( "notcomponent match fails" )
	return 0

    # Does the event have a location that this section is interested in?
    if not configREMatch( config, s, 'location', [evt.Location], True ):
	logger.debug( "location match fails" )
	return 0

    # Does the event have a location that this section wants to avoid?
    if configREMatch( config, s, 'notlocation', [evt.Location], False ):
	logger.debug( "notlocation match fails" )
	return 0

    # Does the event have a systems organiser that this section is interested in?
    if not configREMatch( config, s, 'systems', evt.Systems.split('|'), True ):
	logger.debug( "systems match fails" )
	return 0

    # Does the event have a systems organiser that this section wants to avoid?
    if configREMatch( config, s, 'notsystems', evt.Systems.split('|'), False ):
	logger.debug( "notsystems match fails" )
	return 0

    # Does the event have an ipaddress that this section is interested in?
    if not configREMatch( config, s, 'ipaddress', [evt.ipAddress], True ):
	logger.debug( "ipaddress match fails" )
	return 0

    # Does the event have an ipaddress that this section wants to avoid?
    if configREMatch( config, s, 'notipaddress', [evt.ipAddress], False ):
	logger.debug( "notipaddress match fails" )
	return 0

    # We want this one!
    return 1


# Function to analyse an event and possibly create a troubleticket
def analyseEvent( config, dmd, evt ):
    logger.debug( "analyseEvent" )

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
	logger.warning("Device %s is not in a device group in event %s" % (evt.device, evt.evid))

    # Consider each section in the config file
    for s in config.sections():
        try:
	    if ((s == 'DAEMONSTUFF') or (s == 'AUTOCLEAR')):
		# Ignore the main daemon config section and the history matching section
		# (DEFAULT is automatically skipped)
		continue

	    logger.debug( "Section %s" % (s) )

	    # Compare event against filters - do we want it?
	    if not selectEvent( config, s, evt ):
		continue

	    logger.debug( "creating ticket" )

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
		# we have a string in 'arg' which may contain %var% substitution keys
		# First we must split that string into a list where each %var% is a separate item
		keypattern = re.compile( r'(%[a-z0-9_-]+%)', re.IGNORECASE )
		keylist = re.split( keypattern, arg )
		# Now walk through the list doing the substitutions
		for index2,arg2 in enumerate(keylist):
		    if data.has_key(arg2.lower()):
			keylist[index2] = data[arg2.lower()]
		# Finally, join all that up again and put it back in the main arg list
		ttargs[index] = ''.join( keylist )

	    logger.debug( "command: %s" % ( str(ttargs) ) )

	    try:
		# Run the ticket create script (while passing necessary arguments to it).
		p = subprocess.Popen(ttargs, stdout=subprocess.PIPE)

		if not p:
		    raise TicketError("Unable to run ticket creation command %s" % (ttcommand))

		# Let the command run and collect its output
		(stdoutdata, stderrdata) = p.communicate()
		logger.debug( "TT Script stdout: %s" % (stdoutdata) )
		logger.debug( "TT Script stderr: %s" % (stderrdata) )

	    except OSError as e:
                if e.filename:
		    raise TicketError("Error while running ticket creation command: %s: %s" % (e.filename, e.strerror))
                else:
		    raise TicketError("Error while running ticket creation command: %s" % (e.strerror))

	    # Get the ticket ID
	    ticket = stdoutdata.rstrip()
	    # Sanity check
	    if not re.search( r'[0-9]+', ticket ):
		raise TicketError("No ticket ID returned from troubleticket system")

	    ntickets += 1

	    logger.info("Ticket %s created for event %s" % (ticket, evt.evid))

	    # If ticket was successfully created, acknowledge the event in Zenoss.
	    eventlist = [evt.evid]
	    if evt.eventState == 0:
		try:
		    # Ack
		    dmd.ZenEventManager.manage_setEventStates(1, eventlist)

		    # Update event info
		    update="update status set ownerid='%s'" % ('Ticket ' + ticket)
		    whereClause = "where evid = '%s'" % (evt.evid)
		    reason = 'Trouble Ticket created: ' + ticket
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

        except TicketError as e:
            logger.error( "Ticket creation failed for %s: %s" % (evt.evid, e.errmsg) )
            ticketerror = 1
	    # If this event has not errored before, we need to update the message
	    if 'FAILED' not in evt.ownerid:
	        try:
		    update="update status set ownerid='Ticket FAILED'"
		    whereClause = "where evid = '%s'" % (evt.evid)
		    reason = 'Ticket creation failed'
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

            continue

    if ticketerror: return -1

    # We did it!
    return ntickets


# Daemon code space begins here.
class MyDaemon(Daemon):
    def run(self):

	# Get handle on Zenoss itself
        dmd = ZenScriptBase(connect=True).dmd

        # Configure logging within daemon code space.
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        logger.info('Start of daemon run self')
	logger.info('logfile is %s ' % (logfile))


        # Read in config file.
        config = ConfigParser.ConfigParser()
        config.read([zenconfpath])

        # Gather general config file options in to variables.
        ttcommand = config.get("DAEMONSTUFF", "ttcommand")
        cycletime = config.get("DAEMONSTUFF", "cycletime")

        # Run daemon forever.......
        while True:

	    logger.info( "zentt main loop" )

	    # Keep track of how many tickets we have created
	    numttcreated = 0

            # Events to create new tickets for.....
            # Ticket creation cycle begins here.
            for e in dmd.ZenEventManager.getEventList([], "", "lastTime ASC, firstTime ASC"):

                # Define initial variables for ticket creation cycle.
                ttcreate = 0

		logger.debug( "#### Event %s" % (e.evid) )

		# Get the event details
		evt = dmd.ZenEventManager.getEventDetailFromStatusOrHistory(e.evid)
		if not evt:
		    logger.warning("Event %s not found" % (e.evid))
		    continue

		# Create a ticket for all new events that match defined criteria
		tt = analyseEvent( config, dmd, evt)

		# Update the count of tickets created
		if tt > 0:
		    numttcreated = numttcreated + tt

		# No errors, but the event is new and no ticket was created for it
		if tt == 0:
		    # If no ticket was created then consider clearing the event
		    logger.debug( "Checking AUTOCLEAR" )

		    if selectEvent( config, 'AUTOCLEAR', evt ):
			logger.debug( "Clearing event %s" % (e.evid) )

			try:
			    # Update event info
			    update="update status set summary='%s'" % (evt.summary + ' auto-cleared by zenTT ')
			    whereClause = "where evid = '%s'" % (e.evid)
			    reason = 'Event matches AUTOCLEAR criteria'

			    logger.info( "Clearing event %s: %s" % (e.evid, reason) )

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
                    logger.info('Tickets created: %d', numttcreated)

            # Sleep for the amount of seconds configured in the cycletime setting before starting the next cycle.
            logger.debug('End of cycle - sleeping for %s seconds', cycletime)
            time.sleep(int(cycletime))

# Daemon runtime options are defined here.
if __name__ == "__main__":
	daemon = MyDaemon(pidfile)

        # Grab the option.
	if len(sys.argv) == 2:

                # Option to start the daemon code in the foreground
		if 'fg' == sys.argv[1]:
                        if os.path.exists(zenconfpath):

			    # create console log handler for use in 'fg' mode
			    ch = logging.StreamHandler()
			    ch.setLevel(logging.DEBUG)
			    logFormatter = logging.Formatter(fmt='%(asctime)s %(levelname)s ZenTT: %(message)s',
							    datefmt='%Y-%m-%d %H:%M:%S')
			    ch.setFormatter(logFormatter)
			    logger.addHandler(ch)
			    logger.info('Starting zentt fg')

                            daemon.run()
                        else:
                            print '%s is missing, aborting fg.' % (zenconfpath)

                # Option to start the daemon.
		if 'start' == sys.argv[1]:
                        if os.path.exists(zenconfpath):
                            logger.info('Starting zentt')
                            daemon.start()
                        else:
                            print '%s is missing, aborting start.' % (zenconfpath)

                # Option to stop the daemon.
		elif 'stop' == sys.argv[1]:
                        if os.path.exists(pidfile):
                            logger.info('Deleting PID file %s ...', pidfile)
                            logger.info('zentt shutting down')
                        print 'stopping...'
			daemon.stop()

                # Option to restart the daemon.
		elif 'restart' == sys.argv[1]:
                        if os.path.exists(pidfile):
                            logger.info('Deleting PID file %s ...', pidfile)
                            logger.info('zentt shutting down')
                        print 'stopping...'
			daemon.stop()
                        if os.path.exists(zenconfpath):
                            logger.info('Starting zentt')
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
