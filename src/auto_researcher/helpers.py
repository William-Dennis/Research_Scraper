import subprocess
import time
from threading import Thread
import functools
from functools import wraps


def retry_with_vpn_connect(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            # Attempt to connect VPN and retry
            connect_nordvpn_uk()
            return func(*args, **kwargs)

    return wrapper


def connect_nordvpn_uk():
    print("--------------")
    print("RESTARTING VPN")
    print("--------------")
    try:
        subprocess.run(["nordvpn", "-c", "-g", "United Kingdom"], check=True)
        time.sleep(10)  # wait for connection to establish
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to connect: {e}")


def git_update_commit_push():
    commands = ["git add .", 'git commit -m "updating papers"', "git push"]
    for cmd in commands:
        # Run each git command in the shell
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error running '{cmd}': {result.stderr}")
            break
        else:
            print(result.stdout)


class TimeoutError(Exception):
    """Raised when a function times out"""

    pass


def timeout(seconds=30):
    """Decorator that raises TimeoutError if function doesn't complete in time."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = [TimeoutError(f"{func.__name__} timed out")]

            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    result[0] = e

            thread = Thread(target=target)
            thread.daemon = True
            thread.start()
            thread.join(timeout=seconds)

            if thread.is_alive():
                raise TimeoutError(f"{func.__name__} timed out after {seconds} seconds")

            if isinstance(result[0], Exception):
                raise result[0]
            return result[0]

        return wrapper

    return decorator
