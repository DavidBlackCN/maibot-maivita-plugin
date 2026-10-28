"""Resolve live routes through the official read-only Host capability.

The global bot.qq_account is only an adapter fallback, not route evidence.
This module grants no actor privileges and never sends messages.
"""
import asyncio
import re


async def resolve(ctx, stream_id):
    if type(stream_id) is not str or not 0 < len(stream_id) <= 256:
        raise ValueError('ROUTE_UNRESOLVED')
    rows = await asyncio.wait_for(ctx.chat.get_all_streams(platform='qq'), 2)
    if type(rows) is not list:
        raise ValueError('ROUTE_UNRESOLVED')
    matches = [r for r in rows if type(r) is dict and r.get('session_id') == stream_id]
    if len(matches) != 1:
        raise ValueError('ROUTE_UNRESOLVED')
    route = matches[0]
    account = route.get('account_id')
    scope = route.get('scope')
    if (route.get('platform') != 'qq' or route.get('stream_id') != stream_id
            or type(account) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}', account)
            or (scope is not None and (type(scope) is not str or not 0 < len(scope) <= 256))):
        raise ValueError('ROUTE_UNRESOLVED')
    return route


def matches_message(route, raw):
    if type(raw) is not dict or type(raw.get('message_info')) is not dict:
        return False
    info = raw['message_info']
    group = info.get('group_info')
    user = info.get('user_info')
    if (raw.get('session_id') != route['session_id'] or raw.get('platform') != 'qq'
            or raw.get('account_id') != route['account_id'] or raw.get('scope') != route.get('scope')):
        return False
    if route.get('is_group_session') is True:
        return type(group) is dict and bool(route.get('group_id')) and group.get('group_id') == route['group_id']
    return (route.get('is_group_session') is False and group is None and type(user) is dict
            and bool(route.get('user_id')) and user.get('user_id') == route['user_id'])


async def command_route(ctx, command):
    route = await resolve(ctx, command.get('stream_id'))
    if not matches_message(route, command.get('message')):
        raise ValueError('ROUTE_MISMATCH')
    return route
