"""Load an explicitly mounted WeChat Native configuration without environment keys."""
import json
import os
import stat

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.views.decorators.debug import sensitive_variables

from .wechat import build_wechat_request


@sensitive_variables()
def load_wechat_credentials(path):
    """Read a private, app-owned file; never include its contents in errors."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 32768):
                raise ValueError
            data = stream.read(32769)
        if len(data) > 32768:
            raise ValueError
        config = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object)
        fields = {'enabled', 'app_id', 'mch_id', 'merchant_entity', 'merchant_serial',
                  'private_key', 'public_key', 'public_key_id', 'api_v3_key',
                  'notify_origin', 'payment_ttl_seconds'}
        if (type(config) is not dict or set(config) != fields or config['enabled'] is not True or
                type(config['payment_ttl_seconds']) is not int or
                not 60 <= config['payment_ttl_seconds'] <= 7200 or
                type(config['merchant_entity']) is not str or
                not 1 <= len(config['merchant_entity']) <= 128 or
                not config['merchant_entity'].strip() or
                any(ord(char) < 32 for char in config['merchant_entity']) or
                type(config['api_v3_key']) is not str):
            raise ValueError
        config['api_v3_key'] = config['api_v3_key'].encode('ascii')
        if len(config['api_v3_key']) != 32 or type(config['public_key']) is not str:
            raise ValueError
        public_data = config['public_key'].encode('ascii')
        if len(public_data) > 8192 or not public_data.startswith(b'-----BEGIN PUBLIC KEY-----'):
            raise ValueError
        public = serialization.load_pem_public_key(public_data)
        if not isinstance(public, rsa.RSAPublicKey) or public.key_size < 2048:
            raise ValueError
        # Validate identifiers, origin and merchant private key with the same
        # offline request validator used at dispatch; this sends no request.
        build_wechat_request('native', '0' * 32, mchid=config['mch_id'],
            merchant_serial=config['merchant_serial'], private_key=config['private_key'],
            public_key_id=config['public_key_id'], timestamp=1, nonce='configuration',
            appid=config['app_id'], amount_minor=1, description='Sage local test',
            notify_origin=config['notify_origin'], time_expire='2026-01-01T00:00:00+00:00')
        return config
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError, UnsupportedAlgorithm):
        pass
    raise ValueError('WeChat credential file is invalid, unreadable or incomplete.')
