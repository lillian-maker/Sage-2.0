"""Read an explicitly mounted Alipay configuration without putting keys in the environment."""
import json
import os
import re
import stat
from urllib.parse import urlsplit

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.views.decorators.debug import sensitive_variables


@sensitive_variables()
def load_alipay_credentials(path):
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
        fields = {'enabled', 'app_id', 'seller_id', 'merchant_entity', 'private_key',
                  'public_key', 'payment_ttl_seconds', 'notify_origin'}
        if (type(config) is not dict or set(config) != fields or config['enabled'] is not True or
                type(config['payment_ttl_seconds']) is not int or
                config['payment_ttl_seconds'] <= 0):
            raise ValueError
        for name in ('app_id', 'seller_id'):
            if type(config[name]) is not str or not re.fullmatch(r'[0-9]{1,64}', config[name]):
                raise ValueError
        for name in ('merchant_entity', 'notify_origin', 'private_key', 'public_key'):
            if type(config[name]) is not str or not config[name].strip() or len(config[name]) > 8192:
                raise ValueError
        origin = urlsplit(config['notify_origin'])
        if (origin.scheme != 'https' or not origin.hostname or origin.username is not None or
                origin.password is not None or origin.path or origin.query or origin.fragment or
                not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?', config['notify_origin']) or
                (origin.port is not None and not 1 <= origin.port <= 65535) or
                len(config['notify_origin']+'/api/payments/webhooks/alipay') > 255):
            raise ValueError
        private_data = config['private_key'].encode('ascii')
        public_data = config['public_key'].encode('ascii')
        if (not private_data.startswith(b'-----BEGIN RSA PRIVATE KEY-----') or
                not public_data.startswith(b'-----BEGIN PUBLIC KEY-----')):
            raise ValueError
        private = serialization.load_pem_private_key(private_data, password=None)
        public = serialization.load_pem_public_key(public_data)
        if (not isinstance(private, rsa.RSAPrivateKey) or private.key_size < 2048 or
                not isinstance(public, rsa.RSAPublicKey) or public.key_size < 2048):
            raise ValueError
        return config
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, UnsupportedAlgorithm):
        pass
    # Raise after leaving the handler so the parser exception (which can retain
    # the complete JSON document) is not reachable through __context__.
    raise ValueError('Alipay credential file is invalid, unreadable or incomplete.')
