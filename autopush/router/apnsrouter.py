"""APNS Router"""
import time

import apns
from twisted.logger import Logger
from twisted.internet.threads import deferToThread

from autopush.router.interface import RouterException, RouterResponse


# https://github.com/djacobs/PyAPNs
class APNSRouter(object):
    """APNS Router Implementation"""
    log = Logger()
    apns = None
    messages = {}
    errors = {0: 'No error',
              1: 'Processing error',
              2: 'Missing device token',
              3: 'Missing topic',
              4: 'Missing payload',
              5: 'Invalid token size',
              6: 'Invalid topic size',
              7: 'Invalid payload size',
              8: 'Invalid token',
              10: 'Shutdown',
              255: 'Unknown',
              }

    def _connect(self):
        """Connect to APNS"""
        self.apns = apns.APNs(use_sandbox=self.config.get("sandbox", False),
                              cert_file=self.config.get("cert_file"),
                              key_file=self.config.get("key_file"),
                              enhanced=True)

    def __init__(self, ap_settings, router_conf):
        """Create a new APNS router and connect to APNS"""
        self.ap_settings = ap_settings
        self.config = router_conf
        self.default_title = router_conf.get("default_title", "SimplePush")
        self.default_body = router_conf.get("default_body", "New Alert")
        self._connect()
        self.log.debug("Starting APNS router...")

    def register(self, uaid, router_data, *kwargs):
        """Validate that an APNs instance token is in the ``router_data``"""
        if not router_data.get("token"):
            raise RouterException("No token registered", status_code=500,
                                  response_body="No token registered")
        return router_data

    def amend_msg(self, msg, router_data=None):
        return msg

    def check_token(self, token):
        return (True, token)

    def route_notification(self, notification, uaid_data):
        """Start the APNS notification routing, returns a deferred"""
        router_data = uaid_data["router_data"]
        # Kick the entire notification routing off to a thread
        return deferToThread(self._route, notification, router_data)

    def _route(self, notification, router_data):
        """Blocking APNS call to route the notification"""
        token = router_data["token"]
        custom = {
            "Chid": notification.channel_id,
            "Ver": notification.version,
        }
        if notification.data:
            custom["Msg"] = notification.data
            custom["Con"] = notification.headers["content-encoding"]
            custom["Enc"] = notification.headers["encryption"]

            if "crypto-key" in notification.headers:
                custom["Cryptokey"] = notification.headers["crypto-key"]
            elif "encryption-key" in notification.headers:
                custom["Enckey"] = notification.headers["encryption-key"]

        payload = apns.Payload(alert=router_data.get("title",
                                                     self.default_title),
                               content_available=1,
                               custom=custom)
        now = int(time.time())
        self.messages[now] = {"token": token, "payload": payload}
        # TODO: Add listener for error handling.
        self.apns.gateway_server.register_response_listener(self._error)
        self.apns.gateway_server.send_notification(token, payload, now)

        # cleanup sent messages
        if self.messages:
            for time_sent in self.messages.keys():
                if time_sent < now - self.config.get("expry", 10):
                    del self.messages[time_sent]
        return RouterResponse(status_code=200, response_body="Message sent")

    def _error(self, err):
        """Error handler"""
        if err['status'] == 0:
            self.log.debug("Success")
            del self.messages[err['identifier']]
            return
        self.log.debug("APNs Error encountered: {status}",
                       status=self.errors[err['status']])
        if err['status'] in [1, 255]:
            self.log.debug("Retrying...")
            self._connect()
            resend = self.messages.get(err.get('identifier'))
            if resend is None:
                return
            self.apns.gateway_server.send_notification(resend['token'],
                                                       resend['payload'],
                                                       err['identifier'],
                                                       )
