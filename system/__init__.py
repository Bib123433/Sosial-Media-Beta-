from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = "myheart"

    from system.socket import init_socket
    init_socket(app, socketio)

    from system.cloudinary  import cloud_bp

    app.register_blueprint(cloud_bp,    url_prefix='/cloud')

    # 4) bind socketio ke app
    socketio.init_app(app)

    return app
