_logs=[]; _log_seq=0
import time
def log(line:str):
    global _log_seq; _log_seq+=1
    ts=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())+'Z'
    _logs.append({"id":_log_seq,"line":f"[{ts}] {line}"})
    return _log_seq
def get_logs(since:int=0,limit:int=1000):
    return [x for x in _logs if x["id"]>since][-limit:]
