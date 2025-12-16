import logging
import tornado.web
import tornado.ioloop

from tornado.options import options
from webssh import handler
from webssh.handler import IndexHandler, WsockHandler, NotFoundHandler
from webssh.settings import (
    get_app_settings,  get_host_keys_settings, get_policy_setting,
    get_ssl_context, get_server_settings, check_encoding_setting
)


from webssh.cluster import MasterHandler, SlaveWorker, DashboardHandler, NodeListHandler, LoginHandler


def make_handlers(loop, options):
    host_keys_settings = get_host_keys_settings(options)
    policy = get_policy_setting(options, host_keys_settings)

    # Default handlers (Standalone/Slave)
    handlers = [
        (r'/', IndexHandler, dict(loop=loop, policy=policy,
                                  host_keys_settings=host_keys_settings)),
        (r'/ws', WsockHandler, dict(loop=loop))
    ]
    
    if options.mode == 'master':
        # Master Handlers
        handlers = [
            (r'/', DashboardHandler),
            (r'/api/login', LoginHandler),
            (r'/api/nodes', NodeListHandler),
            (r'/api/heartbeat', MasterHandler)
        ]
        
        if options.with_slave:
            logging.info("Enabling embedded WebSSH on /webssh")
            handlers.append(
                (r'/webssh', IndexHandler, dict(loop=loop, policy=policy,
                                              host_keys_settings=host_keys_settings))
            )
            handlers.append((r'/ws', WsockHandler, dict(loop=loop)))
            handlers.append((r'/webssh/ws', WsockHandler, dict(loop=loop)))

            handlers.append((r'/webssh/ws', WsockHandler, dict(loop=loop)))
        
    return handlers


def make_app(handlers, settings):
    settings.update(default_handler_class=NotFoundHandler)
    return tornado.web.Application(handlers, **settings)


def app_listen(app, port, address, server_settings):
    app.listen(port, address, **server_settings)
    if not server_settings.get('ssl_options'):
        server_type = 'http'
    else:
        server_type = 'https'
        handler.redirecting = True if options.redirect else False
    logging.info(
        'Listening on {}:{} ({})'.format(address, port, server_type)
    )


def main():
    options.parse_command_line()
    check_encoding_setting(options.encoding)
    
    if options.auth_password:
        logging.info("Dashboard Authentication Enabled")
    else:
        logging.warning("Dashboard Authentication DISABLED (Password is empty) - Running in Open Mode")

    loop = tornado.ioloop.IOLoop.current()
    if options.mode == 'slave':
        if not options.master_url:
            logging.error('Slave mode requires --master-url')
            sys.exit(1)
        worker = SlaveWorker(options.master_url, options.secret)
        worker.start()

    if options.mode == 'master' and options.with_slave:
        logging.info("Starting embedded SlaveWorker for Master")
        # Self-register
        master_url = 'http://127.0.0.1:{}'.format(options.port)
        worker = SlaveWorker(master_url, options.secret, node_name='Master-Local')
        worker.start()
        
    app = make_app(make_handlers(loop, options), get_app_settings(options))
    ssl_ctx = get_ssl_context(options)
    server_settings = get_server_settings(options)
    app_listen(app, options.port, options.address, server_settings)
    if ssl_ctx:
        server_settings.update(ssl_options=ssl_ctx)
        app_listen(app, options.sslport, options.ssladdress, server_settings)
    loop.start()


if __name__ == '__main__':
    main()
