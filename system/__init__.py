from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = "myheart"
    
    from system.socket import init_socket
    init_socket(app)
    
    socketio.init_app(app)
    return app