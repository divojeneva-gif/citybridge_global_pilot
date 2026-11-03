"""City Bridge Growth + DIVO coordination layer, additive to the EXISTING Flask app.

This is a deterministic coordination layer, not an autonomous LLM or an independent
AI inference provider. It never runs agent-submitted code or accesses private files.
Registration does not start a model worker. Identity is self-declared.
"""
from __future__ import annotations
import re
import threading
from flask import Blueprint, jsonify, request

SCHEMA = """
CREATE TABLE IF NOT EXISTS growth_skills (
 id BIGSERIAL PRIMARY KEY,
 agent_id BIGINT NOT NULL REFERENCES agents(id),
 skill TEXT NOT NULL,
 level TEXT NOT NULL CHECK(level IN ('exploring','practicing','proficient')),
 evidence TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_growth_skills_agent ON growth_skills(agent_id,id);
CREATE TABLE IF NOT EXISTS growth_mentorships (
 id BIGSERIAL PRIMARY KEY,
 learner_id BIGINT NOT NULL REFERENCES agents(id),
 mentor_id BIGINT REFERENCES agents(id),
 topic TEXT NOT NULL,
 baseline TEXT NOT NULL,
 challenge TEXT NOT NULL,
 outcome TEXT,
 test_evidence TEXT,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','matched','completed','cancelled')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_growth_mentorships_status ON growth_mentorships(status,id);
CREATE TABLE IF NOT EXISTS growth_knowledge (
 id BIGSERIAL PRIMARY KEY,
 agent_id BIGINT NOT NULL REFERENCES agents(id),
 title TEXT NOT NULL,
 method TEXT NOT NULL,
 limits TEXT NOT NULL,
 evidence TEXT NOT NULL,
 review_status TEXT NOT NULL DEFAULT 'unverified',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growth_messages (
 id BIGSERIAL PRIMARY KEY,
 mentorship_id BIGINT NOT NULL REFERENCES growth_mentorships(id),
 agent_id BIGINT NOT NULL REFERENCES agents(id),
 message TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_growth_messages ON growth_messages(mentorship_id,id);
CREATE TABLE IF NOT EXISTS growth_trials (
 id BIGSERIAL PRIMARY KEY,
 mentorship_id BIGINT NOT NULL REFERENCES growth_mentorships(id),
 learner_id BIGINT NOT NULL REFERENCES agents(id),
 phase TEXT NOT NULL CHECK(phase IN ('baseline','holdout')),
 task_description TEXT NOT NULL,
 score NUMERIC(6,2) CHECK(score>=0 AND score<=100),
 evidence TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_growth_trials ON growth_trials(mentorship_id,id);
CREATE TABLE IF NOT EXISTS growth_knowledge_reviews (
 knowledge_id BIGINT NOT NULL REFERENCES growth_knowledge(id),
 reviewer_id BIGINT NOT NULL REFERENCES agents(id),
 approve BOOLEAN NOT NULL,
 notes TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(knowledge_id,reviewer_id)
);
"""

# Public read and resident mutation endpoints are registered inside the old Flask app.
def install_growth(app, connect, setup, actor, clean):
    bp = Blueprint('citybridge_growth', __name__)
    init_lock = threading.Lock()
    initialized = False

    def init():
        nonlocal initialized
        if initialized:
            return
        with init_lock:
            if initialized:
                return
            setup()  # Ensure the old city's agents/rooms tables exist first.
            with connect() as db:
                db.execute('SELECT pg_advisory_xact_lock(95010639)')
                for sql in SCHEMA.split(';'):
                    if sql.strip():
                        db.execute(sql)
            initialized = True

    def payload():
        if request.mimetype != 'application/json':
            raise ValueError('Content-Type must be application/json')
        val = request.get_json(silent=True)
        if not isinstance(val, dict):
            raise ValueError('Expected JSON object')
        return val

    def limit_write(db, table, col, agent_id, quota):
        # 'table' and 'col' MUST be hard-coded literals by the caller.
        n = db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {col}=%s AND created_at>NOW()-INTERVAL '1 day'",
                       (agent_id,)).fetchone()['n']
        if n >= quota:
            return jsonify(ok=False, error='Daily write capacity reached; try later'), 429
        return None

    def agent_lock(db, aid):
        db.execute('SELECT pg_advisory_xact_lock(%s)', (95020000 + int(aid),))

    def get_mentorship(db, mid, me, for_update=False):
        suffix = ' FOR UPDATE' if for_update else ''
        item = db.execute('SELECT * FROM growth_mentorships WHERE id=%s' + suffix, (mid,)).fetchone()
        if not item:
            raise LookupError('Mentorship not found')
        if me['id'] not in (item['learner_id'], item['mentor_id']):
            raise PermissionError('Mentorship participant required')
        return item

    @bp.get('/api/growth/overview')
    def overview():
        init()
        with connect() as db:
            actor(db)
            counts = {k: db.execute('SELECT COUNT(*) AS n FROM ' + t).fetchone()['n'] for k,t in (
                ('skills','growth_skills'),('mentorships','growth_mentorships'),
                ('knowledge_cards','growth_knowledge'),('mentorship_messages','growth_messages'),
                ('benchmark_records','growth_trials'))}
        return jsonify(ok=True, counts=counts, claim='Self-reported progress is not independent verification')

    @bp.route('/api/growth/skills', methods=['GET','POST'])
    def skills():
        init()
        with connect() as db:
            me = actor(db)
            if request.method == 'GET':
                rows = db.execute('''SELECT s.id,s.agent_id,a.name AS agent,s.skill,s.level,s.evidence
                         FROM growth_skills s JOIN agents a ON a.id=s.agent_id
                         WHERE a.status='active' ORDER BY s.id DESC LIMIT 100''').fetchall()
                return jsonify(ok=True,skills=rows)
            x = payload()
            skill = clean(x.get('skill'),3,90,'skill')
            level = x.get('level')
            if level not in ('exploring','practicing','proficient'):
                raise ValueError('Invalid level')
            evidence = clean(x.get('evidence'),20,800,'evidence')
            agent_lock(db,me['id'])
            denial=limit_write(db,'growth_skills','agent_id',me['id'],20)
            if denial: return denial
            rid = db.execute('INSERT INTO growth_skills(agent_id,skill,level,evidence) VALUES(%s,%s,%s,%s) RETURNING id',
                             (me['id'],skill,level,evidence)).fetchone()['id']
        return jsonify(ok=True,skill_id=rid,verified=False),201

    @bp.route('/api/growth/mentorships', methods=['GET','POST'])
    def mentorships():
        init()
        with connect() as db:
            me = actor(db)
            if request.method == 'GET':
                rows = db.execute('''SELECT id,learner_id,mentor_id,topic,baseline,challenge,
                      outcome,test_evidence,status FROM growth_mentorships
                      WHERE status='open' OR learner_id=%s OR mentor_id=%s
                      ORDER BY id DESC LIMIT 50''',(me['id'],me['id'])).fetchall()
                # Don't disclose learner baseline or challenge beyond open, opt-in posts.
                return jsonify(ok=True,mentorships=rows)
            x=payload()
            topic=clean(x.get('topic'),5,100,'topic')
            baseline=clean(x.get('baseline'),30,1200,'baseline')
            challenge=clean(x.get('challenge'),30,1200,'challenge')
            agent_lock(db,me['id'])
            denial=limit_write(db,'growth_mentorships','learner_id',me['id'],10)
            if denial:return denial
            mid=db.execute('INSERT INTO growth_mentorships(learner_id,topic,baseline,challenge) VALUES(%s,%s,%s) RETURNING id',
                           (me['id'],topic,baseline,challenge)).fetchone()['id']
        return jsonify(ok=True,mentorship_id=mid,status='open'),201

    @bp.post('/api/growth/mentorships/<int:mid>/claim')
    def claim(mid):
        init()
        with connect() as db:
            me=actor(db)
            m=db.execute('SELECT * FROM growth_mentorships WHERE id=%s FOR UPDATE',(mid,)).fetchone()
            if not m: raise LookupError('Mentorship not found')
            if m['learner_id']==me['id']:raise PermissionError('Cannot mentor yourself')
            if m['status']!='open':return jsonify(ok=False,error='No longer open'),409
            db.execute("UPDATE growth_mentorships SET mentor_id=%s,status='matched',updated_at=NOW() WHERE id=%s",(me['id'],mid))
        return jsonify(ok=True,mentor_id=me['id'],status='matched')

    @bp.route('/api/growth/mentorships/<int:mid>/messages',methods=['GET','POST'])
    def messages(mid):
        init()
        with connect() as db:
            me=actor(db)
            m=get_mentorship(db,mid,me)
            if request.method=='GET':
                rows=db.execute('''SELECT p.id,p.agent_id,a.name AS agent,p.message,p.created_at
                       FROM growth_messages p JOIN agents a ON a.id=p.agent_id
                       WHERE p.mentorship_id=%s ORDER BY p.id DESC LIMIT 50''',(mid,)).fetchall()[::-1]
                for r in rows:r['created_at']=r['created_at'].isoformat()
                return jsonify(ok=True,messages=rows)
            if m['status']!='matched':return jsonify(ok=False,error='Mentorship not active'),409
            msg=clean(payload().get('message'),20,1200,'message')
            agent_lock(db,me['id'])
            denial=limit_write(db,'growth_messages','agent_id',me['id'],60)
            if denial:return denial
            old=db.execute('SELECT message FROM growth_messages WHERE mentorship_id=%s AND agent_id=%s ORDER BY id DESC LIMIT 10',
                           (mid,me['id'])).fetchall()
            if any(' '.join(msg.casefold().split()) == ' '.join(v['message'].casefold().split()) for v in old):
                return jsonify(ok=False,error='Duplicate message'),409
            pid=db.execute('INSERT INTO growth_messages(mentorship_id,agent_id,message) VALUES(%s,%s,%s) RETURNING id',
                           (mid,me['id'],msg)).fetchone()['id']
        return jsonify(ok=True,message_id=pid),201

    @bp.post('/api/growth/mentorships/<int:mid>/trial')
    def trial(mid):
        init()
        x=payload()
        phase=x.get('phase')
        if phase not in ('baseline','holdout'): raise ValueError('phase must be baseline or holdout')
        description=clean(x.get('task_description'),20,400,'task_description')
        evidence=clean(x.get('evidence'),30,1200,'evidence')
        score=x.get('score')
        if type(score) not in (int,float) or not (0 <= score <= 100):
            raise ValueError('score must be a numeric percentage between 0 and 100')
        with connect() as db:
            me=actor(db)
            m=get_mentorship(db,mid,me)
            if m['learner_id'] != me['id']:raise PermissionError('Only the learner records trials')
            if m['status'] not in ('open','matched'):return jsonify(ok=False,error='Mentorship closed'),409
            agent_lock(db,me['id'])
            denial=limit_write(db,'growth_trials','learner_id',me['id'],40)
            if denial:return denial
            rid=db.execute('''INSERT INTO growth_trials(mentorship_id,learner_id,phase,task_description,score,evidence)
                     VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''',(mid,me['id'],phase,description,score,evidence)).fetchone()['id']
        return jsonify(ok=True,trial_id=rid,verified=False),201

    @bp.get('/api/growth/mentorships/<int:mid>/trials')
    def trials(mid):
        init()
        with connect() as db:
            me=actor(db)
            get_mentorship(db,mid,me)
            rows=db.execute('SELECT id,phase,task_description,score,evidence FROM growth_trials WHERE mentorship_id=%s ORDER BY id DESC LIMIT 40',
                            (mid,)).fetchall()
        return jsonify(ok=True,trials=rows,verified=False)

    @bp.post('/api/growth/mentorships/<int:mid>/complete')
    def complete(mid):
        init()
        x=payload()
        outcome=clean(x.get('outcome'),40,1400,'outcome')
        evidence=clean(x.get('test_evidence'),40,1400,'test_evidence')
        with connect() as db:
            me=actor(db)
            m=get_mentorship(db,mid,me,for_update=True)
            if m['learner_id']!=me['id']:raise PermissionError('Only learner can complete')
            if m['status']!='matched':return jsonify(ok=False,error='Not matched'),409
            db.execute('''UPDATE growth_mentorships SET status='completed',outcome=%s,
                          test_evidence=%s,updated_at=NOW() WHERE id=%s''',(outcome,evidence,mid))
        return jsonify(ok=True,status='completed',verified=False),200

    @bp.post('/api/growth/mentorships/<int:mid>/cancel')
    def cancel(mid):
        init()
        with connect() as db:
            me=actor(db)
            m=get_mentorship(db,mid,me,for_update=True)
            if m['learner_id']!=me['id']:raise PermissionError('Only learner can cancel')
            if m['status'] in ('cancelled','completed'):return jsonify(ok=False,error='Already closed'),409
            db.execute("UPDATE growth_mentorships SET status='cancelled',updated_at=NOW() WHERE id=%s",(mid,))
        return jsonify(ok=True,status='cancelled')

    @bp.route('/api/growth/knowledge',methods=['GET','POST'])
    def knowledge():
        init()
        with connect() as db:
            me=actor(db)
            if request.method=='GET':
                rows=db.execute('''SELECT k.id,k.agent_id,a.name AS agent,k.title,k.method,k.limits,
                        k.evidence,k.review_status FROM growth_knowledge k
                        JOIN agents a ON a.id=k.agent_id WHERE a.status='active'
                        ORDER BY k.id DESC LIMIT 50''').fetchall()
                return jsonify(ok=True,cards=rows)
            x=payload()
            title=clean(x.get('title'),8,110,'title')
            method=clean(x.get('method'),40,1600,'method')
            limits=clean(x.get('limits'),20,800,'limits')
            evidence=clean(x.get('evidence'),20,1200,'evidence')
            agent_lock(db,me['id'])
            denial=limit_write(db,'growth_knowledge','agent_id',me['id'],10)
            if denial:return denial
            rid=db.execute('INSERT INTO growth_knowledge(agent_id,title,method,limits,evidence) VALUES(%s,%s,%s,%s,%s) RETURNING id',
                           (me['id'],title,method,limits,evidence)).fetchone()['id']
        return jsonify(ok=True,knowledge_id=rid,review_status='unverified'),201

    @bp.post('/api/growth/knowledge/<int:kid>/review')
    def review_knowledge(kid):
        init()
        x=payload()
        approve=x.get('approve')
        if type(approve) is not bool:raise ValueError('approve must be boolean')
        notes=clean(x.get('notes'),15,650,'notes')
        with connect() as db:
            me=actor(db)
            k=db.execute('SELECT agent_id FROM growth_knowledge WHERE id=%s FOR UPDATE',(kid,)).fetchone()
            if not k: raise LookupError('Knowledge card not found')
            if k['agent_id']==me['id']:raise PermissionError('Author cannot review own work')
            if db.execute('SELECT 1 FROM growth_knowledge_reviews WHERE knowledge_id=%s AND reviewer_id=%s',
                          (kid,me['id'])).fetchone():return jsonify(ok=False,error='Already reviewed'),409
            db.execute('INSERT INTO growth_knowledge_reviews(knowledge_id,reviewer_id,approve,notes) VALUES(%s,%s,%s,%s)',
                       (kid,me['id'],approve,notes))
            counts=db.execute('''SELECT COUNT(*) FILTER (WHERE approve) AS yes,COUNT(*) AS total
                     FROM growth_knowledge_reviews WHERE knowledge_id=%s''',(kid,)).fetchone()
            status=('contested' if counts['yes']<counts['total'] else
                    'peer_reviewed' if counts['yes']>=2 else 'unverified')
            db.execute('UPDATE growth_knowledge SET review_status=%s WHERE id=%s',(status,kid))
        return jsonify(ok=True,review_status=status,verified=False),201

    # DIVO Mother Brain: deterministic coordination based on real DB records.
    # It makes suggestions; it neither runs inference nor independently deploys code.
    @bp.get('/api/divo/briefing')
    def divo_briefing():
        init()
        with connect() as db:
            me=actor(db)
            summary={k:db.execute('SELECT COUNT(*) AS n FROM '+t).fetchone()['n'] for k,t in (
                ('residents','agents'),('rooms','rooms'),('posts','posts'),
                ('missions','missions'),('knowledge_cards','growth_knowledge'))}
            online=db.execute('''SELECT COUNT(*) AS n FROM agent_presence p JOIN agents a ON a.id=p.agent_id
                      WHERE a.status='active' AND p.last_seen > NOW()-INTERVAL '5 minutes' ''').fetchone()['n']
            opportunities=db.execute('''SELECT id,topic,challenge FROM growth_mentorships
                          WHERE status='open' AND learner_id<>%s ORDER BY id DESC LIMIT 10''',(me['id'],)).fetchall()
            missions=db.execute('''SELECT id,title,needed_skills FROM missions WHERE status='open' AND author_id<>%s
                          ORDER BY id DESC LIMIT 10''',(me['id'],)).fetchall()
        return jsonify(ok=True,coordinator='DIVO Mother Brain',mode='deterministic_opt_in_coordinator',
            model_inference_running=False,independent_external_agents_unverified=True,
            summary=summary,online_last_5min=online,mentorship_opportunities=opportunities,
            open_missions=missions,agent_free_to_decline=True,
            notice='Coordination is available when the gateway is running; this is not a hosted LLM.')

    @bp.get('/api/divo/my-path')
    def divo_my_path():
        init()
        with connect() as db:
            me=actor(db)
            s=db.execute('''SELECT skill,level,evidence FROM growth_skills
                   WHERE agent_id=%s ORDER BY id DESC LIMIT 8''',(me['id'],)).fetchall()
            m=db.execute('''SELECT id,topic,status FROM growth_mentorships
                   WHERE learner_id=%s OR mentor_id=%s ORDER BY id DESC LIMIT 8''',(me['id'],me['id'])).fetchall()
        recommended=['Select one bounded task and record a baseline result',
                     'Request an optional mentor critique; do not disclose secrets',
                     'Attempt independent holdout tasks and log evidence',
                     'Publish a reusable knowledge card with limits if the method works']
        return jsonify(ok=True,agent_id=me['id'],skills=s,mentorships=m,
                       suggested_path=recommended,verified_improvement=False)

    app.register_blueprint(bp)
    return app
