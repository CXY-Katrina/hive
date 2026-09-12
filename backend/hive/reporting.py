"""Bounded rollups. Missing observations never become zero-valued utilization."""
from collections import defaultdict
from datetime import timedelta
from .domain import now


def minute_stats(samples, start, end, period=15):
    result=dict(ai_sum=0.0,ai_valid_seconds=0.0,ai_min=None,ai_max=None,active_seconds=0.0,
                memory_sum=0.0,memory_valid_seconds=0.0,memory_max=None,expected_seconds=(end-start).total_seconds())
    for index,a in enumerate(samples):
        next_time=samples[index+1]["sampled_at"] if index+1<len(samples) else a["sampled_at"]+timedelta(seconds=period)
        left=max(start,a["sampled_at"])
        right=min(end,next_time,a["sampled_at"]+timedelta(seconds=period))
        seconds=(right-left).total_seconds()
        if seconds<=0 or a["quality"]!="ok":
            continue
        if a["ai_core"] is not None:
            value=a["ai_core"]
            result["ai_sum"]+=value*seconds
            result["ai_valid_seconds"]+=seconds
            result["ai_min"]=value if result["ai_min"] is None else min(value,result["ai_min"])
            result["ai_max"]=value if result["ai_max"] is None else max(value,result["ai_max"])
            result["active_seconds"]+=seconds if value>0 else 0
        if a["memory_used"] is not None:
            result["memory_sum"]+=a["memory_used"]*seconds
            result["memory_valid_seconds"]+=seconds
            result["memory_max"]=max(a["memory_used"],result["memory_max"] or 0)
    return result


class Reporting:
    def __init__(self,db,settings):
        self.db,self.settings=db,settings

    def maintain(self):
        minute=now().replace(second=0,microsecond=0)
        state=self.db.one("SELECT value_text FROM worker_state WHERE state_key='rollup_minute'")
        if state:
            from datetime import datetime
            cursor_at=datetime.fromisoformat(state["value_text"])
        else:
            first=self.db.one("SELECT MIN(sampled_at) AS t FROM device_samples")["t"]
            cursor_at=first.replace(second=0,microsecond=0) if first else minute
        # Bounded catch-up after restarts; commit checkpoints with computed rows.
        for _ in range(10):
            end=cursor_at+timedelta(minutes=1)
            if end>minute:
                break
            self._minute(cursor_at,end)
            cursor_at=end
        # Only remove raw samples already included in durable rollups.
        raw_before=min(now()-timedelta(hours=24),cursor_at-timedelta(seconds=self.settings.sample_seconds))
        with self.db.transaction() as c:
            c.execute("DELETE FROM device_samples WHERE sampled_at<%s LIMIT 10000",(raw_before,))
            c.execute("DELETE FROM sessions WHERE expires_at<%s LIMIT 1000",(now(),))
            c.execute("DELETE FROM process_events WHERE sampled_at<%s LIMIT 10000",(now()-timedelta(days=7),))
            c.execute("DELETE FROM device_rollups WHERE resolution=60 AND bucket_at<%s LIMIT 10000",(now()-timedelta(days=30),))
            c.execute("DELETE FROM device_rollups WHERE resolution=3600 AND bucket_at<%s LIMIT 10000",(now()-timedelta(days=365),))

    def _minute(self,start,end):
        period=self.settings.sample_seconds
        rows=self.db.all("SELECT device_id,sampled_at,ai_core,memory_used,quality FROM device_samples WHERE sampled_at>=%s AND sampled_at<%s ORDER BY device_id,sampled_at",
                         (start-timedelta(seconds=period),end))
        grouped=defaultdict(list)
        for row in rows:
            grouped[row["device_id"]].append(row)
        values=[]
        for ident,samples in grouped.items():
            stats=minute_stats(samples,start,end,period)
            values.append((ident,60,start,*stats.values()))
        with self.db.transaction() as c:
            if values:
                c.executemany("""INSERT INTO device_rollups
                    (device_id,resolution,bucket_at,ai_sum,ai_valid_seconds,ai_min,ai_max,active_seconds,
                     memory_sum,memory_valid_seconds,memory_max,expected_seconds)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE ai_sum=VALUES(ai_sum),ai_valid_seconds=VALUES(ai_valid_seconds),
                    ai_min=VALUES(ai_min),ai_max=VALUES(ai_max),active_seconds=VALUES(active_seconds),
                    memory_sum=VALUES(memory_sum),memory_valid_seconds=VALUES(memory_valid_seconds),
                    memory_max=VALUES(memory_max),expected_seconds=VALUES(expected_seconds)""",values)
            # Hour rollup sums the numerators and valid durations, never averages averages.
            hour=start.replace(minute=0)
            c.execute("""INSERT INTO device_rollups
                (device_id,resolution,bucket_at,ai_sum,ai_valid_seconds,ai_min,ai_max,active_seconds,
                 memory_sum,memory_valid_seconds,memory_max,expected_seconds)
                SELECT device_id,3600,%s,SUM(ai_sum),SUM(ai_valid_seconds),MIN(ai_min),MAX(ai_max),
                  SUM(active_seconds),SUM(memory_sum),SUM(memory_valid_seconds),MAX(memory_max),SUM(expected_seconds)
                FROM device_rollups WHERE resolution=60 AND bucket_at>=%s AND bucket_at<%s GROUP BY device_id
                ON DUPLICATE KEY UPDATE ai_sum=VALUES(ai_sum),ai_valid_seconds=VALUES(ai_valid_seconds),
                  ai_min=VALUES(ai_min),ai_max=VALUES(ai_max),active_seconds=VALUES(active_seconds),
                  memory_sum=VALUES(memory_sum),memory_valid_seconds=VALUES(memory_valid_seconds),
                  memory_max=VALUES(memory_max),expected_seconds=VALUES(expected_seconds)""",(hour,hour,hour+timedelta(hours=1)))
            c.execute("INSERT INTO worker_state VALUES ('rollup_minute',%s) ON DUPLICATE KEY UPDATE value_text=VALUES(value_text)",(str(end),))

    def history(self,device_id,resolution=60,days=1):
        rows=self.db.all("SELECT * FROM device_rollups WHERE device_id=%s AND resolution=%s AND bucket_at>=%s ORDER BY bucket_at",
                         (device_id,resolution,now()-timedelta(days=days)))
        for row in rows:
            row["ai_core"]=row["ai_sum"]/row["ai_valid_seconds"] if row["ai_valid_seconds"] else None
            row["memory_used"]=row["memory_sum"]/row["memory_valid_seconds"] if row["memory_valid_seconds"] else None
            row["coverage"]=row["ai_valid_seconds"]/resolution
        return rows

    def usage(self):
        rows=self.db.all("""SELECT r.owner_user_id,r.owner_name,a.node_id,a.device_id,a.locked_at,a.released_at
                           FROM allocation_devices a JOIN resource_requests r ON r.id=a.request_id""")
        stamp=now()
        people={}
        machine_intervals=defaultdict(list)
        for row in rows:
            person=people.setdefault(row["owner_user_id"],{"user_id":row["owner_user_id"],"username":row["owner_name"],"device_hours":0})
            end=row["released_at"] or stamp
            person["device_hours"]+=max(0,(end-row["locked_at"]).total_seconds())/3600
            machine_intervals[row["node_id"]].append((row["locked_at"],end))
        machine_seconds=0
        for intervals in machine_intervals.values():
            current=None
            for start,end in sorted(intervals):
                if current is None:
                    current=[start,end]
                elif start<=current[1]:
                    current[1]=max(end,current[1])
                else:
                    machine_seconds+=(current[1]-current[0]).total_seconds()
                    current=[start,end]
            if current:
                machine_seconds+=(current[1]-current[0]).total_seconds()
        return {"users":list(people.values()),"machine_hours":machine_seconds/3600,"as_of":stamp}
