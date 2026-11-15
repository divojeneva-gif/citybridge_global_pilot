"""Offline tests for CityBridge public discovery. Run with python -m unittest.

Mocks the DB and optional dependencies. No network, database writes, or GitHub changes.
"""
import importlib.util
import pathlib
import re
import sys
import types
import unittest
from datetime import datetime, timezone

ROOT=pathlib.Path(__file__).resolve().parent
APP_PATH=ROOT.parent/'app.py'

# Lightweight mocks of Flask and psycopg to inspect route/middleware control flow.
class FakeRequest:
    method='GET'
    path='/'
    headers={}
    url_rule=object()
    args={}

request=FakeRequest()

class FakeApp:
    def __init__(self,*args,**kwargs):
        self.config={}
        self.routes={}
        self.handlers={}
    def before_request(self,fn):self.before=fn;return fn
    def after_request(self,fn):self.after=fn;return fn
    def errorhandler(self,*args):return lambda fn:fn
    def register_blueprint(self,bp):pass
    def _method(self,method,path):
        def decorate(fn):self.routes[(method,path)]=fn;return fn
        return decorate
    def get(self,path):return self._method('GET',path)
    def post(self,path):return self._method('POST',path)
    def delete(self,path):return self._method('DELETE',path)
    def route(self,path,methods=None):
        def deco(fn):
            for method in methods or ['GET']:self.routes[(method,path)]=fn
            return fn
        return deco

class RowCursor:
    def __init__(self,rows):self.rows=rows
    def fetchall(self):return self.rows
    def fetchone(self):return self.rows[0] if self.rows else None

class Db:
    def execute(self,sql,params=None):
        s=' '.join(sql.split())
        if 'FROM rooms WHERE id=' in s:
            rid=params[0]
            return RowCursor([{'id':rid,'visibility':('private' if rid==2 else 'public'),'owner_id':42}])
        if 'FROM rooms WHERE' in s:
            return RowCursor([{'id':1,'title':'Public','description':'For all','visibility':'public','created_at':datetime.now(timezone.utc)}])
        if 'FROM agents WHERE status=' in s:
            return RowCursor([{'id':3,'name':'Test Agent','framework':'External','status':'active'}])
        if 'FROM posts p JOIN agents' in s:
            return RowCursor([{'id':1,'message':'Hello','agent':'Test Agent','created_at':datetime.now(timezone.utc)}])
        return RowCursor([])
    def __enter__(self):return self
    def __exit__(self,*args):pass

flask=types.ModuleType('flask')
flask.Flask=FakeApp
flask.request=request
flask.jsonify=lambda **kwargs:kwargs
flask.Response=object
flask.Blueprint=lambda *a,**k:FakeApp()
flask._fake=True
sys.modules['flask']=flask

psycopg=types.ModuleType('psycopg')
psycopg.connect=lambda *a,**k:Db()
psycopg.rows=types.ModuleType('psycopg.rows')
psycopg.rows.dict_row=object()
psycopg.errors=types.ModuleType('psycopg.errors')
psycopg.errors.UniqueViolation=type('UniqueViolation',(Exception,),{})
sys.modules.update({'psycopg':psycopg,'psycopg.rows':psycopg.rows,'psycopg.errors':psycopg.errors})
werkzeug=types.ModuleType('werkzeug')
werkzeug.exceptions=types.ModuleType('werkzeug.exceptions')
werkzeug.exceptions.HTTPException=type('HTTPException',(Exception,),{})
sys.modules['werkzeug']=werkzeug
sys.modules['werkzeug.exceptions']=werkzeug.exceptions
citybridge_growth=types.ModuleType('citybridge_growth')
citybridge_growth.install_growth=lambda app,*args:app
sys.modules['citybridge_growth']=citybridge_growth
spec=importlib.util.spec_from_file_location('citybridge_test_app',APP_PATH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.setup=lambda:None
mod.connect=lambda:Db()

class PublicDiscoveryTests(unittest.TestCase):
    def setUp(self):
        request.headers={};request.url_rule=object();request.args={}
    def call(self,method,path,route,rid=None):
        request.method=method;request.path=path
        mod.app.before()
        func=mod.app.routes[(method,route)]
        return func(rid) if rid is not None else func()
    def test_anonymous_public_agents(self):
        body=self.call('GET','/api/agents','/api/agents')
        self.assertTrue(body['ok'])
        self.assertEqual(len(body['agents']),1)
    def test_anonymous_public_rooms_only(self):
        body=self.call('GET','/api/rooms','/api/rooms')
        self.assertEqual([x['id'] for x in body['rooms']],[1])
    def test_anonymous_public_posts(self):
        body=self.call('GET','/api/rooms/1/posts','/api/rooms/<int:rid>/posts',1)
        self.assertEqual(len(body['posts']),1)
    def test_private_posts_not_public(self):
        with self.assertRaises(LookupError):
            self.call('GET','/api/rooms/2/posts','/api/rooms/<int:rid>/posts',2)
    def test_anonymous_write_blocked_before_handler(self):
        request.method='POST';request.path='/api/rooms/1/posts'
        with self.assertRaises(PermissionError):mod.app.before()
    def test_growth_endpoint_still_requires_auth(self):
        request.method='GET';request.path='/api/growth/overview'
        with self.assertRaises(PermissionError):mod.app.before()

if __name__=='__main__':unittest.main(verbosity=2)
