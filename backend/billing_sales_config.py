"""Read the reviewed commercial switches from one explicitly mounted file.

Keys and certificates never belong here: this file only states what may be sold,
through which approved channel, and under which approved purchase terms.
"""
import json
import os
import stat

ALIPAY_CAPABILITIES = {'one_time', 'term_monthly', 'term_annual'}
FLAGS = ('sales_enabled', 'alipay_live_enabled', 'alipay_egress_proxy', 'credits_purchase_enabled')


def load_billing_sales(path):
    """Return Django setting values, or fail startup on any unexpected content."""
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
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o022 or info.st_size > 262144:
                raise ValueError
            data = stream.read(262145)
        if len(data) > 262144:
            raise ValueError
        config = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object)
        if type(config) is not dict or set(config) != {*FLAGS, 'approved_channels', 'terms_documents'}:
            raise ValueError
        if any(type(config[name]) is not bool for name in FLAGS):
            raise ValueError
        channels = config['approved_channels']
        # Only the Alipay page-payment adapter has a production path today.
        if type(channels) is not dict or set(channels) - {'alipay'}:
            raise ValueError
        for channel in channels.values():
            if (type(channel) is not dict or
                    set(channel) != {'enabled', 'currency', 'merchant_entity', 'regions', 'capabilities'} or
                    type(channel['enabled']) is not bool or channel['currency'] != 'CNY' or
                    type(channel['merchant_entity']) is not str or
                    not 1 <= len(channel['merchant_entity'].strip()) <= 128 or
                    channel['regions'] != ['CN'] or type(channel['capabilities']) is not list or
                    not channel['capabilities'] or
                    any(type(item) is not str for item in channel['capabilities']) or
                    set(channel['capabilities']) - ALIPAY_CAPABILITIES or
                    len(set(channel['capabilities'])) != len(channel['capabilities'])):
                raise ValueError
        documents = config['terms_documents']
        if type(documents) is not dict:
            raise ValueError
        for version, document in documents.items():
            if (not version.strip() or len(version) > 80 or type(document) is not dict or
                    set(document) != {'approved', 'zh', 'en'} or type(document['approved']) is not bool):
                raise ValueError
            for language in ('zh', 'en'):
                content = document[language]
                if (type(content) is not dict or set(content) != {'title', 'paragraphs'} or
                        type(content['title']) is not str or not content['title'].strip() or
                        type(content['paragraphs']) is not list or not content['paragraphs'] or
                        any(type(text) is not str or not text.strip() for text in content['paragraphs'])):
                    raise ValueError
        return {
            'BILLING_SALES_ENABLED': config['sales_enabled'],
            'BILLING_APPROVED_CHANNELS': channels,
            'BILLING_ALIPAY_LIVE_ENABLED': config['alipay_live_enabled'],
            'BILLING_ALIPAY_EGRESS_PROXY': 'http://egress:3128' if config['alipay_egress_proxy'] else '',
            'BILLING_CREDITS_PURCHASE_ENABLED': config['credits_purchase_enabled'],
            'BILLING_TERMS_DOCUMENTS': documents,
        }
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        pass
    raise ValueError('Billing sales configuration file is invalid, unreadable or incomplete.')
