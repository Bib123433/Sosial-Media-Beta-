from bson import ObjectId
from flask_socketio import emit, join_room, leave_room
from flask import request, session
from system import socketio
from groq import Groq
from dotenv import load_dotenv
import os
import replicate


print("GROQ API KEY:", os.getenv("GROQ_API_KEY"))

online_users = {}
sid_to_user = {}
load_dotenv()
BOT_ID = "BibAI"
 
ai_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
replicate_client = replicate.Client(api_token="r8_V9fMr0HmdbiylBgzqZmp5or1h316W5Z3kcCpC")




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
        sender_id = data.get("sender_id")
        receiver_id = data.get("receiver_id")
        message = data.get("message", "").strip()
        if not message:
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
        
        if receiver_id == BOT_ID:
            if message.lower().startswith("gambar "):
                prompt = message[7:].strip()

                try:

                    output = replicate_client.run(
                        "google/imagen-4",
                        input={"prompt": prompt}
                    )
                    image_url = str(output)
                    bot_teks = f"Gambar berhasil dibuat untuk:\n{prompt}\n{image_url}"

                except Exception as e:
                    bot_teks = f"Gagal membuat gambar:\n{str(e)}"

            else:
                # Jalankan AI teks (Deepseek)
                ai_resp = ai_client.chat.completions.create(
                    model="deepseek-r1-distill-llama-70b",
                    messages=[{"role": "user", "content": message}]
                )
                bot_teks = ai_resp.choices[0].message.content.strip()

            # Kirim balasan ke user
            bot_msg = {
                "sender_id": BOT_ID,
                "receiver_id": sender_id,
                "message": bot_teks,
                "full_name": "BibAI",
                "profile_picture": "/static/assets/images/user/05.jpg"
            }
            emit("new_message", bot_msg, room=sender_id)
            
    @socketio.on('call')
    def handle_call(data):
        from_user = data.get('from')
        to_user = data.get('to')
        offer = data.get('offer')

        if not from_user or not to_user or not offer:
            print("❌ Invalid call data:", data)
            return

        print(f"📞 {from_user} is calling {to_user}")

        sid_target = online_users.get(to_user, {}).get("sid")
        if sid_target:
            emit('incoming_call', {
                'from': from_user,
                'offer': offer,
            }, room=sid_target)
            print(f"✅ Sent incoming_call to {to_user}")
        else:
            print(f"⚠️ Target user {to_user} not online or SID not found.")


    @socketio.on('answer')
    def handle_answer(data):
        to_user = data.get('to')
        answer = data.get('answer')

        if not to_user or not answer:
            print("❌ Invalid answer data:", data)
            return

        sid_target = online_users.get(to_user, {}).get("sid")
        if sid_target:
            emit('answer', {'answer': answer}, room=sid_target)
            print(f"✅ Sent answer to {to_user}")
        else:
            print(f"⚠️ Cannot send answer to {to_user} — not connected.")


    @socketio.on('ice-candidate')
    def handle_ice_candidate(data):
        to_user = data.get('to')
        candidate = data.get('candidate')

        if not to_user or not candidate:
            print("❌ Invalid ICE data:", data)
            return

        sid_target = online_users.get(to_user, {}).get("sid")
        if sid_target:
            emit('ice-candidate', {'candidate': candidate}, room=sid_target)
            print(f"✅ Sent ICE candidate to {to_user}")
        else:
            print(f"⚠️ Cannot send ICE candidate to {to_user} — not connected.")



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
