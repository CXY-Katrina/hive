import argparse
import base64
import logging
import os
from .config import Settings


def main():
    parser=argparse.ArgumentParser(prog="hive")
    parser.add_argument("command",choices=["keygen","migrate","api","worker"])
    parser.add_argument("--host",default="0.0.0.0")
    parser.add_argument("--port",type=int,default=18000)
    parser.add_argument("--env-file",default=".env")
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command=="keygen":
        print(base64.urlsafe_b64encode(os.urandom(32)).decode())
        return
    settings=Settings.from_env(args.env_file)
    if args.command=="migrate":
        from .db import Database
        Database(settings).migrate()
    elif args.command=="api":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings=settings),host=args.host,port=args.port,
                    ssl_certfile=args.ssl_certfile,ssl_keyfile=args.ssl_keyfile)
    else:
        from .services import build_services
        from .worker import Worker
        worker=Worker(build_services(settings))
        try:
            worker.run()
        except KeyboardInterrupt:
            worker.stop.set()


if __name__=="__main__":
    main()
