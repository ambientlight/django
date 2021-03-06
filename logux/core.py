"""
Core of Logux Django
"""
from __future__ import annotations

import json
import logging
import re
from abc import abstractmethod, ABC
from copy import deepcopy
from datetime import datetime
from typing import List, Callable, Optional, Dict, NewType, Union, Sequence, Any

import requests
from django.conf import settings

from logux import LOGUX_PROTOCOL_VERSION
from logux.exceptions import LoguxProxyException

# Logux requests \ response data format
LoguxValue = NewType("LoguxValue", Sequence[Union[Dict, str]])

Action = Dict[str, Any]
logger = logging.getLogger(__name__)

LOGUX_SUBSCRIBE = 'logux/subscribe'
LOGUX_UNDO = 'logux/undo'


def protocol_version_is_supported(version: int) -> bool:
    """ Check possibility of support protocol version.
    :param version: the proto version from request

    :return: True if version is supported
    """
    return version == LOGUX_PROTOCOL_VERSION


class Meta:  # pylint: disable=too-many-instance-attributes
    """ Logux meta: https://logux.io/guide/concepts/meta/
    TODO: add docs about comp:
      https://github.com/logux/django/issues/12#issuecomment-612394901
    """

    def __init__(self, raw_meta: Dict[str, str]):
        # Take raw meta and parse all required to properties
        self._raw_meta = raw_meta
        # Keep in mind, if self._raw_meta will change all properties do not be reassignment,
        #   so, do not change self._raw_meta during Meta instance lifecycle

        self._uid: List[str] = self._get_uid()

        self.id: str = self._raw_meta['id']
        self.time_from_id = self._get_time_from_id()

        self.user_id: str = self._get_user_id()
        self.client_id: str = self._get_client_id()
        self.node_id: Optional[str] = self._get_node_id()
        self.time: datetime = self._get_time()

        self.subprotocol: str = self._get_subprotocol()

    def __getitem__(self, item):
        return self._raw_meta[item]

    def __eq__(self, o) -> bool:
        return self.time == o.time and self.id == o.id

    def __ne__(self, o) -> bool:
        return not self.__eq__(o)

    def __lt__(self, other: Meta) -> bool:
        # pylint: disable=no-else-return,too-many-return-statements
        # <
        if self.get_raw_meta():
            if not other.get_raw_meta():
                return False
            elif not self.get_raw_meta() and other.get_raw_meta():
                return True
        elif not self.get_raw_meta() and other.get_raw_meta():
            return True

        if self.time > other.time:
            return False
        elif self.time < other.time:
            return True

        if self.id > other.id:
            return False
        elif self.id < other.id:
            return True

        if self.time_from_id > other.time_from_id:
            return False
        elif self.time_from_id < other.time_from_id:
            return True

        return False

    def __gt__(self, other: Meta) -> bool:
        # pylint: disable=no-else-return,too-many-return-statements
        # >
        if self.get_raw_meta() and not other.get_raw_meta():
            return True
        elif not self.get_raw_meta() and other.get_raw_meta():
            return False

        if self.time < other.time:
            return False
        elif self.time > other.time:
            return True

        if self.id < other.id:
            return False
        elif self.id > other.id:
            return True

        if self.time_from_id < other.time_from_id:
            return False
        elif self.time_from_id > other.time_from_id:
            return True

        return False

    # Helpers
    def _get_uid(self):
        try:
            uid = self._raw_meta['id'].split(' ')[1].split(':')
        except IndexError:
            raise ValueError(f'wrong meta id format: {self._raw_meta["id"]}')
        return uid

    def _get_user_id(self) -> str:
        """ Get user id from mata.id.
         For example, if meta.id is '1560954012838 38:Y7bysd:O0ETfc 0',
         then user_id is '38'
         """
        return self._uid[0]

    def _get_client_id(self) -> str:
        """ Get client id from mata.id.
         For example, if meta.id is '1560954012838 38:Y7bysd:O0ETfc 0',
         then client_id is '38:Y7bysd'
         """
        return ':'.join(self._uid[:2])

    def _get_node_id(self) -> Optional[str]:
        """ Get node id from mata.id if exist.
         For example, if meta.id is '1560954012838 38:Y7bysd:O0ETfc 0',
         then client_id is 'O0ETfc'

         If UID does not contain node_id None will be returned
         """
        return self._uid[-1] if len(self._uid) == 3 else None

    def _get_time(self) -> datetime:
        """ Get time from mata in Python datetime type.
         For example, if meta is {'id': "1560954012838 38:Y7bysd 0", 'time': 1560954012838},
         then time is 'datetime.datetime(2019, 6, 20, 0, 20, 12, 838000)'
        """
        return datetime.fromtimestamp(int(self._raw_meta['time']) / 1e3)

    def _get_time_from_id(self) -> datetime:
        """ Get time from `id` of meta in Python datetime type.
         For example, if meta is {'id': "1560954012838 38:Y7bysd 0", 'time': 1560954012838},
         then time from id is 'datetime.datetime(2019, 6, 20, 0, 20, 12, 838000)', that means
         datetime from meta.id[0]
         """
        return datetime.fromtimestamp(int(self.id.split(' ')[0]) / 1e3)

    def _get_subprotocol(self):
        return self._raw_meta.get('subprotocol')

    def get_raw_meta(self) -> Dict:
        """ Get the copy of raw meta dict """
        return deepcopy(self._raw_meta)

    def get_json(self) -> str:
        """ Get raw meta and convert it to JSON """
        return json.dumps(self._raw_meta)


def logux_add(action: Action, raw_meta: Optional[Dict] = None) -> None:
    """ `logux_add` is low level API function to send any actions and meta into Logux server.
    If `raw_meta` is None just empty dict will be passed to Logux server. Logux server
    will set `id` and `time` on this side.

    Keep in mind, in the current version `logux_add` is sync.

    For more information: https://logux.io/node-api/#log-add

    :param action: action dict
    :param raw_meta: meta dict (not Meta instance)

    TODO: extract this exception into custom error class -> logux/exception.py
    :raises: base Exception() if Logux Proxy returns non 200 response code

    :return: None
    """
    command = {
        "version": LOGUX_PROTOCOL_VERSION,
        "secret": settings.LOGUX_CONTROL_SECRET,
        "commands": [
            [
                "action",
                action,
                raw_meta or {}
            ]
        ]
    }

    logger.debug('logux_add action %s with meta %s to Logux', action, raw_meta or {})

    r = requests.post(url=settings.LOGUX_URL, json=command)
    logger.debug('Logux answer is %s: %s', r.status_code, r.text)

    if r.status_code != 200:
        logger.error('`logux_add` to Logux is failed! err: %s: %s', r.status_code, r.text)
        raise LoguxProxyException(f'Non 200 response from Logux Proxy (logux_add): {r.status_code}: {r.text}')


class Command(ABC):
    """ Logux Command abstract class.
    All type of Logux Commands should be inheritance from this one.

    Required ony one method `apply()` witch executing command and return LoguxValue
      with an answer or error a message.
    """

    @abstractmethod
    def apply(self) -> List[LoguxValue]:
        """
         This method consistently apply all Action methods inside of try/catch and construct List of
        LoguxValue's with action methods results or error messages.

        :return: list of results of applying all actions methods
        """
        raise NotImplementedError()


class AuthCommand(Command):
    """ Logux Auth Command provide way to check is the User authenticated.

    TODO: this class should be ActionCommand probably
    """
    user_id: str
    token: str
    auth_id: str

    def __init__(self, cmd_body: Dict[str, str], logux_auth: Callable[[str, str], bool]):
        """ Construct Auth cmd from raw logux command.

        :param cmd_body: raw logux command, like ["auth", "38", "good-token", "gf4Ygi6grYZYDH5Z2BsoR"]
        :type cmd_body: List[str]
        :param logux_auth: function to prove user is authenticated,
          type hint: `logux_auth(user_id: str, token: str) -> bool`. `logux_auth` function will be injected from
          settings.LOGUX_AUTH_FUNC (should be provided by consumer)
        :type logux_auth: Callable[[str, str], bool]
        """
        _, self.user_id, self.token, self.auth_id = cmd_body
        self.logux_auth = logux_auth

    def apply(self) -> List[LoguxValue]:
        """ Applying auth command

        :returns:  `authenticated` or `denied` action dependently if user is authenticated.
        """
        is_authenticated: bool = self.logux_auth(self.user_id, self.token)
        logger.warning('user: %s is not authenticated', self.user_id)
        auth_id: str = self.auth_id
        return [LoguxValue(['authenticated' if is_authenticated else 'denied', auth_id])]


class ActionCommand(Command):
    """ Logux Action Command provide way to handle actions from Logux Proxy.
    """
    # `action_type` is a required property, if the property does not defined
    #    DefaultActionDispatcher will raise ValueError('`action_type` attribute is required for all Actions') Exception
    action_type: str

    def __init__(self, cmd_body: List):
        """ Construct Action cmd from raw logux command.

        :param cmd_body: raw logux cmd, like:
           [
              "action",                                                         // action_type
              { type: 'user/rename', user: 38, name: 'New' },                   // cmd_body[1]
              { id: "1560954012838 38:Y7bysd:O0ETfc 0", time: 1560954012838 }   // cmd_body[2]
            ]
        :type cmd_body: List[Action]
        """
        self._action: Action = cmd_body[1]
        self._meta: Meta = Meta(cmd_body[2])

    @property
    def action(self):
        """ Get copy of Action. Do not change internal action state from outside. """
        return deepcopy(self._action)

    @property
    def meta(self):
        """ Get copy of Meta. Do not change internal meta state from outside. """
        return deepcopy(self._meta)

    def send_back(self, action: Action, raw_meta: Optional[Dict] = None) -> None:
        """ Sand action with meta back to Logux. Will add `clients` from original action to the meta.
        For more information: https://logux.io/guide/concepts/action/#adding-actions-on-the-server

        :param action: any logux action
        :type action: Action
        :param raw_meta: optional additional mata
        :type raw_meta: Optional[Dict]
        """
        raw_meta = {} if raw_meta is None else raw_meta
        logux_add(action, {'clients': [self.meta.client_id], **raw_meta})

    def undo(self, reason: Optional[str] = 'error', extra: Optional[Dict] = None):
        """ Logux undo action. https://logux.io/guide/concepts/action/#loguxundo

        :param reason: describes the reason for reverting
        :type reason: str
        :param extra: optional additional data
        :type extra: Dict
        """
        undo_action = {
            'type': LOGUX_UNDO,
            'id': self.meta.id,
            'reason': reason,
            **extra  # type: ignore
        }

        raw_meta = self.meta.get_raw_meta()
        undo_raw_meta = {
            'status': 'processed',

            'users': raw_meta.get('users'),
            'nodes': raw_meta.get('nodes'),
            'clients': raw_meta.get('clients', []) + [self.meta.client_id],
            'reasons': raw_meta.get('reasons'),
            'channels': raw_meta.get('channels')
        }

        # reduce None keys
        undo_meta = {k: v for (k, v) in undo_raw_meta.items() if v is not None}

        logux_add(undo_action, undo_meta)

    # Required and optional action methods (these methods should be implemented by consumer)
    def _finally(self, action: Action, meta: Meta) -> LoguxValue:  # pylint: disable=unused-argument,no-self-use
        """ Callback which will be run on the end of action/subscription processing or on an error """
        return LoguxValue([])

    @abstractmethod
    def access(self, action: Action, meta: Meta) -> bool:
        """ `access` is required method and should contain code for checking user permissions.

        :param action: logux action
        :type action: Action
        :param meta: logux meta
        :type meta: Meta

        :returns: does current user have permission for apply this action?
        """
        raise NotImplementedError()

    def resend(self, action: Action, meta: Optional[Meta]) -> Dict:  # pylint: disable=unused-argument,no-self-use
        """ `resend` should return recipients for this action.
        It should look like:
        {'channels': ['users/38']}
        and may content fields: channels, users, nodes, clients.

        For more information: https://logux.io/node-api/#resend

        :param action: logux action
        :type action: Action
        :param meta: logux meta
        :type meta: Meta

        :returns: dict with recipients
        """
        return {}

    def process(self, action: Action, meta: Meta) -> None:
        """ `process` should contain consumer business code. If it raised exception,
        self.apply will return error action automatically. If `process` return error action
        Logux server will eval `undo` by this side.

        :param action: logux action
        :type action: Action
        :param meta: logux meta
        :type meta: Meta
        """
        pass

    def apply(self) -> List[LoguxValue]:
        applying_result = []

        # resend
        resend_result = ['resend', self.meta.id, self.resend(self._action, self._meta)]
        applying_result.append(resend_result)

        # access
        try:
            access_result = ['approved' if self.access(self._action, self._meta) else 'denied', self._meta.id]
        except Exception as access_err:  # pylint: disable=broad-except
            access_result = ['error', self._meta.id, f'{access_err}']

        applying_result.append(access_result)

        # process
        if access_result[0] == 'approved':
            try:
                self.process(self._action, self._meta)
                process_result = ['processed', self._meta.id]
            except Exception as process_err:  # pylint: disable=broad-except
                process_result = ['error', self._meta.id, f'{process_err}']

            applying_result.append(process_result)

        # finally
        try:
            self._finally(self._action, self._meta)
            finally_result = []
        except Exception as finally_err:  # pylint: disable=broad-except
            finally_result = ['error', self._meta.id, f'{finally_err}']

        applying_result.append(finally_result)

        # TODO: what wrong with this type?
        # noinspection PyTypeChecker
        return [r for r in applying_result if len(r) != 0]  # type: ignore


class ChannelCommand(ActionCommand):
    """ Logux Subscribe Action Command provide way to handle subscription actions from Logux Proxy.

    For more information: https://logux.io/protocols/backend/examples/#subscription

    Subscription actions should look like:
    [
        "action",
        { type: 'logux/subscribe', channel: '38/name' },
        { id: "1560954012858 38:Y7bysd:O0ETfc 0", time: 1560954012858 }
    ]
    """
    # `channel_pattern` is required property, if property does not defined
    #   DefaultSubscriptionsDispatcher will raise
    #   ValueError('`channel_pattern` attribute is required for `logux/subscription` Actions') Exception
    action_type = LOGUX_SUBSCRIBE
    # regexp, like in urls.py
    channel_pattern: str

    def __init__(self, cmd_body: List[Action]):
        super().__init__(cmd_body)
        self.channel = cmd_body[1]['channel']
        self.params = self._parse_params()

    def _parse_params(self) -> Dict:
        return re.match(self.channel_pattern, self.channel).groupdict()  # type: ignore

    @classmethod
    def is_match(cls, channel: str) -> bool:
        """ Check if the Dispatcher contains channel handler """
        return re.match(cls.channel_pattern, channel) is not None  # type: ignore

    # Required and optional action methods (these methods should be implemented by consumer)
    @abstractmethod
    def load(self, action: Action, meta: Meta) -> None:
        """ `load` should contain consumer code for applying subscription.
        Generally this method is almost the same as `process`. If it raised exception,
        self.apply will return error action automatically. If `load` return error action
        Logux server will eval `undo` by this side.

        :param action: logux action
        :type action: Action
        :param meta: logux meta
        :type meta: Meta
        """
        pass

    def apply(self) -> List[LoguxValue]:
        applying_result = []

        # access
        try:
            access_result = ['approved' if self.access(self._action, self._meta) else 'denied', self._meta.id]
        except Exception as access_err:  # pylint: disable=broad-except
            access_result = ['error', self._meta.id, f'{access_err}']

        # load
        if access_result[0] == 'approved':
            try:
                self.load(self._action, self._meta)
                load_result = ['processed', self._meta.id]
            except Exception as load_err:  # pylint: disable=broad-except
                load_result = ['error', self._meta.id, f'{load_err}']

            applying_result.append(LoguxValue(load_result))

        return applying_result


class UnknownAction(ActionCommand):
    """ Action for generation `unknownAction` error.
    Will be used and evaluated if actions dispatcher
    got unexpected action type """

    def access(self, action: Action, meta: Optional[Meta]) -> bool:
        return False

    def apply(self) -> List[LoguxValue]:
        return [LoguxValue(['unknownAction', self._meta.id])]
