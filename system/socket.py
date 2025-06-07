from bson import ObjectId
from flask_socketio import emit, join_room, leave_room
from flask import request, session
from system import socketio

online_users = {}
sid_to_user = {}        # sid -> user_id

def init_socket(socketio, mongo):
    users_coll       = mongo.db.user
    friends_req_coll = mongo.db.friend_requests
    
    @socketio.on("connect")
    def on_connect(auth):
        user_id = auth.get("user_id")
        full_name = auth.get("full_name")
        print("Socket.IO connect SID:", request.sid)
        print("Auth data:", auth)

        user = users_coll.find_one({"_id": ObjectId(user_id)})
        pic  = user.get("profile_picture", "/static/default.png") if user else "/static/default.png"
            
        online_users[user_id] = {
                "sid": request.sid,
                "full_name": full_name,
                "profile_picture": pic
            }
        sid_to_user[request.sid] = user_id

        join_room(user_id)
        emit("user_connected", {"user_id": user_id, "full_name": full_name}, broadcast=True)

    @socketio.on("request_online_users")
    def send_online_users():
        me = sid_to_user.get(request.sid)
        if not me:
            return
        
        accepted = friends_req_coll.find({
            "$or": [
                {"sender_id": me, "status": "accepted"},
                {"receiver_id": me, "status": "accepted"}
            ]
        })
        
        friend_ids = set()
        for fr in accepted:
            friend_ids.add(fr["receiver_id"] if fr["sender_id"] == me else fr["sender_id"])
            
        friends_online = [
            {
              "user_id":        uid,
              "full_name":      info["full_name"],
              "profile_picture":info["profile_picture"]
            }
            for uid, info in online_users.items()
            if uid in friend_ids
        ]

        emit("online_users", friends_online)



    @socketio.on("send_message")
    def handle_send_message(data):
        print("Receive message:", data)  # Log
        sender_id = data.get("sender_id")
        receiver_id = data.get("receiver_id")
        message = data.get("message", "").strip()
        if not message:
            print("Empty message received. Ignored.")
            return

        full_name = online_users.get(sender_id, {}).get("full_name", "Unknown")
        profile_picture = online_users.get(sender_id, {}).get("profile_picture", "")

        # Normalisasi path agar bisa dipakai langsung di <img src="">
        if profile_picture:
            profile_picture = profile_picture.replace("\\", "/")
            if not profile_picture.startswith("static/"):
                profile_picture = f"static/{profile_picture}"
            profile_picture = f"/{profile_picture}"  # agar bisa dipakai langsung di HTML
        else:
            profile_picture = "/static/default-avatar.png"

        message_data = {
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "message": message,
            "full_name": full_name,
            "profile_picture": profile_picture
        }

        # Kirim ke penerima dan pengirim
        emit("new_message", message_data, room=receiver_id)
        emit("new_message", message_data, room=sender_id)

    @socketio.on("disconnect")
    def on_disconnect():
        sid = request.sid
        uid = sid_to_user.pop(sid, None)
        if uid:
            online_users.pop(uid, None)
            leave_room(uid)
            emit("user_disconnected", {"user_id": uid}, broadcast=True)
            
def kirim_notifikasi(socketio, to_user_id, notif_data):
    socketio.emit("notify", notif_data, room=to_user_id)
