#!/usr/bin/env python3
"""Protocol-v1 handler: fixed read-only Ice endpoints, JSON stdin/stdout."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 8192
OPERATIONS = {'ice_status':'status','ice_query':'query','ice_detail':'detail','ice_explain':'explain'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Tool request exceeds 8 KB')
        request = json.loads(raw)
        operation = OPERATIONS.get(request.get('tool'))
        if operation is None:
            raise ValueError('Unknown Ice tool')
        arguments = request.get('arguments', {})
        if not isinstance(arguments, dict):
            raise ValueError('Arguments must be a JSON object')
        url = os.environ.get('ICE_DASHBOARD_URL','http://127.0.0.1:5175').rstrip('/')
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/')):
            raise ValueError('ICE_DASHBOARD_URL must be an HTTP(S) origin without credentials or path')
        url += '/api/bopbop/' + operation
        if arguments:
            url += '?' + urllib.parse.urlencode(arguments)
        headers = {'Accept':'application/json'}
        token = os.environ.get('ICE_BOPBOP_TOKEN')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        # Ignore ambient proxy config; never forward the token through redirects.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request(url, headers=headers)
        try:
            response = opener.open(req, timeout=7)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if 'application/json' not in response.headers.get('Content-Type',''):
                raise ValueError('Ice returned a non-JSON response')
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError('Ice response exceeds 8 KB')
        result = json.loads(body)
        if not isinstance(result, dict) or result.get('schema_version') != 1:
            raise ValueError('Unsupported Ice API response schema')
        output = json.dumps(result, ensure_ascii=False, separators=(',',':'), allow_nan=False)
        if len(output.encode()) > MAX_BYTES:
            raise ValueError('Ice response exceeds 8 KB')
    except (OSError, ValueError, TypeError, AttributeError) as error:
        output = json.dumps({'schema_version':1,'ok':False,'state':'unavailable',
                             'error':str(error)[:240]},ensure_ascii=False)
    print(output)


if __name__ == '__main__':
    main()
