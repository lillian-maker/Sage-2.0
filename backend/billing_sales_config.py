"""Read the reviewed commercial switches from one explicitly mounted file.

Keys and certificates never belong here: this file only states what may be sold,
through which approved channel, and under which approved purchase terms.
"""
import json
import os
import stat

# Page payment (Alipay) and Native QR (WeChat Pay) settle single payments only.
CHANNEL_CAPABILITIES = {'one_time', 'term_monthly', 'term_annual'}
FLAGS = ('sales_enabled', 'alipay_live_enabled', 'alipay_egress_proxy', 'credits_purchase_enabled')
WECHAT_FLAGS = ('wechat_live_enabled', 'wechat_egress_proxy')


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
        # WeChat switches are optional as a pair so files written before WeChat Pay still load.
        wechat = WECHAT_FLAGS[0] in config if type(config) is dict else False
        flags = FLAGS+WECHAT_FLAGS if wechat else FLAGS
        if type(config) is not dict or set(config) != {*flags, 'approved_channels', 'terms_documents'}:
            raise ValueError
        if any(type(config[name]) is not bool for name in flags):
            raise ValueError
        channels = config['approved_channels']
        if type(channels) is not dict or set(channels) - {'alipay', 'wechat'} or ('wechat' in channels and not wechat):
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
                    set(channel['capabilities']) - CHANNEL_CAPABILITIES or
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
        result = {
            'BILLING_SALES_ENABLED': config['sales_enabled'],
            'BILLING_APPROVED_CHANNELS': channels,
            'BILLING_ALIPAY_LIVE_ENABLED': config['alipay_live_enabled'],
            'BILLING_ALIPAY_EGRESS_PROXY': 'http://egress:3128' if config['alipay_egress_proxy'] else '',
            'BILLING_CREDITS_PURCHASE_ENABLED': config['credits_purchase_enabled'],
            'BILLING_TERMS_DOCUMENTS': documents,
        }
        if wechat:
            result['BILLING_WECHAT_LIVE_ENABLED'] = config['wechat_live_enabled']
            result['BILLING_WECHAT_EGRESS_PROXY'] = 'http://egress:3128' if config['wechat_egress_proxy'] else ''
        return result
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        pass
    raise ValueError('Billing sales configuration file is invalid, unreadable or incomplete.')
