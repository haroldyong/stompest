# -*- coding: iso-8859-1 -*-
import socket
import time

from collections import namedtuple
from itertools import cycle
from random import choice, random
from re import compile

from stompest.error import StompConnectTimeout

class StompSession(object):
    def __init__(self, uri, stompFactory):
        self.subscriptions = []
        self._maxReconnectAttempts = None
        self._config = StompConfiguration(uri) # syntax of uri: cf. stompest.util
        stomps = [stompFactory(broker['host'], broker['port']) for broker in self._config.brokers]
        self._stomps = ((choice(stomps) if self._config.options['randomize'] else stomp) for stomp in cycle(stomps))
    
    def connections(self):
        options = self._config.options
        self._cutoff = time.time() + (options['maxReconnectDelay'] / 1000.0)
        self._reconnectDelay = self._reconnectDelay = options['initialReconnectDelay'] / 1000.0
        if self._maxReconnectAttempts is None:
            self._maxReconnectAttempts = options['startupMaxReconnectAttempts']
        else:
            self._maxReconnectAttempts = options['maxReconnectAttempts']
        self._reconnectAttempts = -1
        while True:
            yield self._stomps.next(), self._delay()

    def subscribe(self, dest, headers):
        subscription = (dest, dict(headers))
        if subscription not in self.subscriptions:
            self.subscriptions.append(subscription)
    
    def unsubscribe(self, dest, headers):
        self.subscriptions.remove((dest, dict(headers)))
        
    def _delay(self):
        options = self._config.options
        if (self._maxReconnectAttempts != -1) and (self._reconnectAttempts >= self._maxReconnectAttempts):
            raise StompConnectTimeout('Reconnect timeout: %d attempts'  % self._maxReconnectAttempts)
        self._reconnectAttempts += 1
        if self._reconnectAttempts == 0:
            return 0
        remainingTime = self._cutoff - time.time()
        if remainingTime <= 0:
            raise StompConnectTimeout('Reconnect timeout: %d ms'  % options['maxReconnectDelay'])
        delay = abs(min(self._reconnectDelay + (random() * options['reconnectDelayJitter'] / 1000.0), remainingTime))
        self._reconnectDelay *= (options['backOffMultiplier'] if options['useExponentialBackOff'] else 1)
        return delay
    
class StompConfiguration(object):
    _localHostNames = set([
        'localhost',
        '127.0.0.1',
        socket.gethostbyname(socket.gethostname()),
        socket.gethostname(),
        socket.getfqdn(socket.gethostname())
    ])
    
    _configurationOption = namedtuple('_configurationOption', ['parser', 'default'])
    _bool = {'true': True, 'false': False}.__getitem__
    
    _FAILOVER_PREFIX = 'failover:'
    _REGEX_URI = compile('^(?P<protocol>tcp)://(?P<host>[^:]+):(?P<port>\d+)$')
    _REGEX_BRACKETS =  compile('^\((?P<uri>.+)\)$')
    _SUPPORTED_OPTIONS = { # cf. http://activemq.apache.org/failover-transport-reference.html
        'initialReconnectDelay': _configurationOption(int, 10) # how long to wait before the first reconnect attempt (in ms)
        , 'maxReconnectDelay': _configurationOption(int, 30000) # the maximum amount of time we ever wait between reconnect attempts (in ms)
        , 'useExponentialBackOff': _configurationOption(_bool, True) # should an exponential backoff be used between reconnect attempts
        , 'backOffMultiplier': _configurationOption(float, 2.0) # the exponent used in the exponential backoff attempts
        , 'maxReconnectAttempts': _configurationOption(int, -1) # -1 is default and means retry forever, 0 means don't retry (only try connection once but no retry), >0 means the maximum number of reconnect attempts before an error is sent back to the client
        , 'startupMaxReconnectAttempts': _configurationOption(int, 0) # if not 0, then this is the maximum number of reconnect attempts before an error is sent back to the client on the first attempt by the client to start a connection, once connected the maxReconnectAttempts option takes precedence
        , 'reconnectDelayJitter': _configurationOption(int, 0) # jitter in ms by which reconnect delay is blurred in order to avoid stampeding
        , 'randomize': _configurationOption(_bool, True) # use a random algorithm to choose the the URI to use for reconnect from the list provided
        , 'priorityBackup': _configurationOption(_bool, False) # if set, prefer local connections to remote connections
        #, 'backup': _configurationOption(_bool, False), # initialize and hold a second transport connection - to enable fast failover
        #, 'timeout': _configurationOption(int, -1), # enables timeout on send operations (in miliseconds) without interruption of reconnection process
        #, 'trackMessages': _configurationOption(_bool, False), # keep a cache of in-flight messages that will flushed to a broker on reconnect
        #, 'maxCacheSize': _configurationOption(int, 131072), # size in bytes for the cache, if trackMessages is enabled
        #, 'updateURIsSupported': _configurationOption(_bool, True), # determines whether the client should accept updates to its list of known URIs from the connected broker
    }
    
    def __init__(self, uri, login='', passcode=''):
        self._parse(uri)
        self.login = login
        self.passcode = passcode
        
    def _parse(self, uri):
        self.uri = uri
        try:
            (uri, _, options) = uri.partition('?')
            if uri.startswith(self._FAILOVER_PREFIX):
                (_, _, uri) = uri.partition(self._FAILOVER_PREFIX)
            
            try:
                self._setOptions(options)
            except Exception, msg:
                raise ValueError('invalid options: %s' % msg)
            
            try:
                self._setBrokers(uri)
            except Exception, msg:
                raise ValueError('invalid broker(s): %s' % msg)
            
        except ValueError, msg:
            raise ValueError('invalid uri: %s [%s]' % (self.uri, msg))
    
    def _setBrokers(self, uri):
        brackets = self._REGEX_BRACKETS.match(uri)
        uri = brackets.groupdict()['uri'] if brackets else uri
        _brokers = [self._REGEX_URI.match(u).groupdict() for u in uri.split(',')]
        for broker in _brokers:
            broker['port'] = int(broker['port'])
        if self.options['priorityBackup']:
            _brokers.sort(key=lambda b: b['host'] in self._localHostNames, reverse=True)
        self.brokers = _brokers

    def _setOptions(self, options=None):
        _options = dict((k, o.default) for (k, o) in self._SUPPORTED_OPTIONS.iteritems())
        if options:
            _options.update((k, self._SUPPORTED_OPTIONS[k].parser(v)) for (k, _, v) in (o.partition('=') for o in options.split(',')))
        self.options = _options

