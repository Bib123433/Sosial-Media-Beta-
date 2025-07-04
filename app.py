from flask import Flask, g, request, jsonify, render_template, session, redirect, url_for, flash
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from datetime import datetime
import os
from functools import wraps
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from humanize import naturaltime
from flask_mail import Mail, Message
import random, string
from PIL import Image
from system.socket import online_users
from system.socket import init_socket
import re
import json
from system.socket import kirim_notifikasi
from system import cloudinary   
import cloudinary.uploader
from groq import Groq
from dotenv import load_dotenv
import hashlib, base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import replicate
import html




app = Flask(__name__)
app.secret_key = "rahasia?"

# MongoDB configuration: pastikan pakai 1 database yang sama untuk register & login
app.config["MONGO_URI"] = (
    "mongodb+srv://AyangFreya:AyangElla@projectayang.c1mw30u.mongodb.net/"
    "Media?retryWrites=true&w=majority"
)
mongo = PyMongo(app)

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
load_dotenv()
ai_client = Groq(api_key=os.getenv("GROQ_API_KEY"))



socketio = SocketIO(app, cors_allowed_origins="*")

# hubungkan socketio & mongo ke handler
init_socket(socketio, mongo)

@app.template_filter('naturaltime')
def naturaltime_filter(value):
    import humanize
    import datetime
    now = datetime.datetime.utcnow()
    return humanize.naturaltime(now - value)

@app.before_request
def load_notifications():
    if "user_id" in session:
        uid = ObjectId(session["user_id"])
        count_unread = mongo.db.notifications.count_documents({
            "to_user_id": uid,
            "is_read": False
        })
        # Simpan di g untuk diakses di template, misalnya:
        g.unread_notifications = count_unread
    else:
        g.unread_notifications = 0


def get_user_reaction(post, user_id):
    for react_type, users in post.get("reactions", {}).items():
        if user_id in users:
            return react_type
    return None

def send_request_friend(sender_id, receiver_id):
    # Cek apakah sudah ada permintaan pertemanan
    existing_request = mongo.db.friend_requests.find_one({
        "sender_id": sender_id,
        "receiver_id": receiver_id
    })
    if not existing_request:
        mongo.db.friend_requests.insert_one({
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "status": "pending"
        })
        
        return True
    return False


def serialize_doc(doc):
    doc["_id"] = str(doc["_id"])
    return doc

def decrypt_message(encrypted, password):
    # 1) Buang whitespace / pastikan '+' tetap utuh
    encrypted = encrypted.strip().replace(' ', '+')

    try:
        ct_bytes = base64.b64decode(encrypted)
    except Exception:
        return "[error decoding]"

    # kalau nggak pakai salted header, kembalikan langsung
    if not ct_bytes.startswith(b"Salted__"):
        try:
            return ct_bytes.decode("utf-8")
        except:
            return "[error decoding]"

    salt = ct_bytes[8:16]
    data = ct_bytes[16:]

    # helper KDF yang bisa flip order
    def derive(pass_first=True):
        d = b""
        key_iv = b""
        while len(key_iv) < 32 + 16:
            if pass_first:
                block = hashlib.md5(d + password.encode() + salt).digest()
            else:
                block = hashlib.md5(d + salt + password.encode()).digest()
            d = block
            key_iv += block
        return key_iv[:32], key_iv[32:48]

    # coba dua‑dua: pass+salt dulu, kalau gagal coba salt+pass
    for pass_first in (True, False):
        key, iv = derive(pass_first)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        try:
            pt = unpad(cipher.decrypt(data), AES.block_size)
            return pt.decode("utf-8")
        except ValueError:
            continue

    return "[error decoding]"
    
def accept_request_friend(sender_id, receiver_id):
    # Cek apakah sudah ada permintaan pertemanan
    existing_request = mongo.db.friend_requests.find_one({
        "sender_id" : sender_id,
        "receiver_id": receiver_id,
        "status": "pending"
        })
    
    if existing_request:
        # Ubah status permintaan menjadi diterima
        mongo.db.friend_requests.update_one(
            {'_id': existing_request['_id']},
            {'$set': {'status': 'accepted'}}
        )
        # Tambahkan ke daftar teman
        mongo.db.friends.insert_one({
            'user_id': sender_id,
            'friend_id': receiver_id
        })
        
def get_user_pic(user_id):
    try:
        user = mongo.db.user.find_one({"_id": ObjectId(user_id)})
        if not user:
            return url_for('static', filename='default.png')
        return user.get("profile_picture", url_for('static', filename='default.png'))
    except Exception as e:
        print("get_user_pic error:", e)
        return url_for('static', filename='default.png')

          
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'megawatichan123@gmail.com'
app.config['MAIL_PASSWORD'] = 'ofps ykvl dahw vwcz'
app.config['MAIL_DEFAULT_SENDER'] = 'megawatichan123@gmail.com'
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_DEBUG'] = True
app.config['MAIL_SUPPRESS_SEND'] = False
app.config['MAIL_ASCII_ATTACHMENTS'] = False
app.config['MAIL_MAX_EMAILS'] = None

mail = Mail(app)

POST_PER_PAGE = 5
replicate_client = replicate.Client(api_token="r8_V9fMr0HmdbiylBgzqZmp5or1h316W5Z3kcCpC")


# Decorator untuk proteksi route, cek session['user_id']
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Silakan login terlebih dahulu.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

# Route: Login
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = mongo.db.user.find_one({"email": email})  # <- FIXED

        if user and check_password_hash(user.get("password"), password):
            session.clear()
            session["user_id"] = str(user.get("_id"))
            session["email"] = user.get("email")
            session["full_name"] = user.get("full_name")
            return redirect(url_for("dashboard"))
        else:
            flash("Email atau password salah.")
            return redirect(url_for("login"))

    return render_template("login/sign-in.html")


# Route: Register
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name")
        email = request.form.get("email")
        password = request.form.get("password")

        # Validasi duplikat
        if mongo.db.user.find_one({"email": email}):  # <- FIXED
            flash("Email sudah terdaftar.")
            return redirect(url_for("register"))

        # Hash & simpan
        hashed_pw = generate_password_hash(password)
        mongo.db.user.insert_one({
            "full_name": full_name,
            "email": email,
            "password": hashed_pw
        })

        flash("Registrasi berhasil! Silakan login.")
        return redirect(url_for("login"))

    return render_template('login/sign-up.html')



@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email")
        user = mongo.db.user.find_one({"email": email})
        
        if user:
            token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
            mongo.db.user.update_one({"_id": user["_id"]}, {"$set": {"reset_token": token}})
            
            reset_link = url_for('reset_password', token=token, _external=True)
            msg = Message("Reset Password", recipients=[email])
            msg.body = f"Klik Link Ini Untuk Reset Password Kamu: {reset_link}"
            mail.send(msg)
        
        return redirect(url_for("reset_password_success"))

    return render_template("recover_password/resetpw.html")

@app.route("/reset-password/success")
def reset_password_success():
    return render_template("recover_password/confirmmail.html")

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = mongo.db.user.find_one({"reset_token": token})
    
    if not user:
        flash("Token tidak valid atau sudah digunakan.")
        return redirect(url_for("login"))

    if request.method == "POST":
        new_password = request.form.get("password")
        hashed_pw = generate_password_hash(new_password)
        
        mongo.db.user.update_one(
            {"_id": user["_id"]},
            {"$set": {"password": hashed_pw}, "$unset": {"reset_token": ""}}
        )
        flash("Password berhasil direset! Silakan login.")
        return redirect(url_for("login"))
    
    return render_template("recover_password/reset-password.html", token=token)


@app.route("/post", methods=["GET", "POST"])
@login_required
def post():
    if request.method == "POST":
        # 1. Ambil field dari form
        text    = request.form.get("text")
        image   = request.files.get("image")
        privasi = request.form.get("privasi", "Publik")

        image_url = None
        if image and image.filename:
            upload_result = cloudinary.uploader.upload(image)
            image_url = upload_result.get("secure_url")
            print('gambar berhasil di upload', image_url)

        try:
            tag_list = json.loads(request.form.get("tag_users", "[]"))
        except json.JSONDecodeError:
            tag_list = []

        tag_users = []
        for tid in tag_list:
            if not ObjectId.is_valid(tid):
                continue
            u = mongo.db.user.find_one(
                {"_id": ObjectId(tid)},
                {"full_name": 1}
            )
            if u:
                tag_users.append({
                    "_id": u["_id"],
                    "full_name": u["full_name"]
                })

        post_data = {
            "user_id":  ObjectId(session["user_id"]),
            "full_name": session["full_name"],
            "email":     session["email"],
            "text":      text,
            "image":     image_url,
            "privasi":   privasi,
            "tag_users": tag_users,              
            "timestamp": datetime.utcnow(),
            "reactions": {
                "like":  [],
                "love":  [],
                "haha":  [],
                "sad":   [],
                "angry": []
            }
        }
        result = mongo.db.posts.insert_one(post_data)
        post_id = result.inserted_id

       
        sender = mongo.db.user.find_one(
            {"_id": ObjectId(session["user_id"])},
            {"profile_picture": 1}
        )
        foto_pengirim = sender.get("profile_picture", "/static/default.png")

        for tag in tag_users:
            to_user_id = tag["_id"]

            notification = {
                "type":       "tag",
                "user_id":    ObjectId(session["user_id"]),
                "to_user_id": to_user_id,
                "post_id":    post_id,
                "timestamp":  datetime.utcnow(),
                "is_read":    False
            }
            mongo.db.notifications.insert_one(notification)

            notif_data = {
                "type":            "tag",
                "full_name":       session["full_name"],
                "profile_picture": foto_pengirim,
                "post_id":         str(post_id),
                "timestamp":       notification["timestamp"].isoformat()
            }
            kirim_notifikasi(
                socketio,
                str(to_user_id),
                notif_data
            )

    return redirect(url_for("dashboard"))

@app.route("/chat", methods=["GET", "POST"])
@login_required
def chat():
    user_id = session.get("user_id")

    if request.method == "POST":
        data = request.get_json()

        if data and "message" in data and "receiver_id" not in data:
            message = data.get("message")

            # === Cek jika permintaan generate gambar ===
            if message.lower().startswith("gambar:"):
                prompt = message[7:].strip()
                try:
                    output = replicate_client.run(
                        "black-forest-labs/flux-dev",
                        input={"prompt": prompt}
                    )
                    image_url = str(output)
                    reply = f"<img src='{image_url}' style='max-width:100%; border-radius:10px; width:400px;'>"
                except Exception as e:
                    reply = f"Gagal membuat gambar: {str(e)}"

            else:
                response = ai_client.chat.completions.create(
                    model="deepseek-r1-distill-llama-70b",
                    messages=[{"role": "user", "content": message}]
                )
                ai_reply = response.choices[0].message.content
                reply = ai_reply.replace("<think>", "").replace("</think>", "")

            # Simpan riwayat ke database
            mongo.db.chat_history.insert_one({
                "user_id": user_id,
                "message": message,
                "reply": reply,
                "timestamp": datetime.utcnow()
            })

            return jsonify({"reply": reply})

        # === User-to-User Chat ===
        elif {"sender_id", "receiver_id", "message"}.issubset(data.keys()):
            sender_id   = data["sender_id"]
            receiver_id = data["receiver_id"]
            message = html.escape(data["message"])

            if not all([sender_id, receiver_id, message]):
                return jsonify({"error": "Data tidak lengkap"}), 400

            mongo.db.user_chats.insert_one({
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "message": message,
                "timestamp": datetime.utcnow()
            })

            return jsonify({"status": "Pesan disimpan", "encrypted": message})

    
    chat_logs = list(mongo.db.chat_history.find({"user_id": user_id}).sort("timestamp", 1))
    
   
    
    current_user = mongo.db.user.find_one({"_id": ObjectId(user_id)})
    users = list(mongo.db.user.find())

    accepted_friends = list(mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": user_id, "status": "accepted"},
            {"receiver_id": user_id, "status": "accepted"}
        ]
    }))

    friend_ids = []
    for f in accepted_friends:
        if f["sender_id"] == user_id:
            friend_ids.append(f["receiver_id"])
        else:
            friend_ids.append(f["sender_id"])

    friend_requests = list(mongo.db.friend_requests.find({
        "receiver_id": user_id,
        "status": "pending"
    }))
    pending_count = len(friend_requests)

    sender_ids = [ObjectId(req["sender_id"]) for req in friend_requests]
    senders = list(mongo.db.user.find({"_id": {"$in": sender_ids}}))
    senders_dict = {str(sender["_id"]): sender for sender in senders}

    for req in friend_requests:
        req["sender_data"] = senders_dict.get(req["sender_id"])

    friend_users = list(mongo.db.user.find({
        "_id": {"$in": [ObjectId(fid) for fid in friend_ids]}
    }))

    friend_list_data = []
    for f in friend_users:
        pic = f.get("profile_picture", "").replace("\\", "/")
        pic = re.sub(r'^/*static/*', '', pic)
        if not pic.strip():
            pic = "default.png"
        if not pic.startswith("uploads/"):
            pic = f"uploads/{pic}"
        pic_url = url_for('static', filename=pic)

        friend_list_data.append({
            "_id": str(f["_id"]),
            "full_name": f.get("full_name", ""),
            "profile_picture": pic_url
        })

    return render_template("chat/chat.html",
                           full_name=session.get("full_name"),
                           email=session.get("email"),
                           req=friend_requests,
                           friend_list_data=friend_list_data,
                           user=current_user,
                           chat_logs=chat_logs,
                           chats=[])  # 🔥 Kirim array kosong
    

@app.route("/chat/history/<receiver_id>")
@login_required
def get_chat_history(receiver_id):
    user_id = session.get("user_id")
    AES_KEY = "$afitri&D1N1"

    chats = list(mongo.db.user_chats.find({
        "$or": [
            {"sender_id": user_id, "receiver_id": receiver_id},
            {"sender_id": receiver_id, "receiver_id": user_id}
        ]
    }).sort("timestamp", 1))

    for chat in chats:
        encrypted = chat.get("message", "")
        try:
            chat["message"] = decrypt_message(encrypted, AES_KEY)
        except Exception as e:
            chat["message"] = "❗Pesan terenkripsi (gagal dekripsi)"
            print("Decryption error:", e)

        chat["_id"] = str(chat["_id"])
        chat["timestamp"] = chat["timestamp"].isoformat()
        chat["sender_pic"] = get_user_pic(chat["sender_id"])
        chat["receiver_pic"] = get_user_pic(chat["receiver_id"])

    return jsonify(chats)




@app.route("/notifications", methods=["GET"])
@login_required
def get_notifications():
    user_id = ObjectId(session["user_id"])
    try:
        docs = mongo.db.notifications.find(
            {"to_user_id": user_id}
        ).sort("timestamp", -1).limit(100)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    list_notif = []
    for d in docs:
        user_pengirim = mongo.db.user.find_one({"_id": d.get("user_id")})
        if user_pengirim:
            nama_pengirim = user_pengirim.get("full_name", "Seseorang")
            foto_pengirim = user_pengirim.get("profile_picture", "/static/default.png")
        else:
            nama_pengirim = "Seseorang"
            foto_pengirim = "/static/default.png"

        waktu = ""
        if d.get("timestamp"):
            try:
                waktu = d["timestamp"].strftime("%d %b %Y %H:%M")
            except Exception:
                waktu = str(d["timestamp"])

        list_notif.append({
            "_id": str(d.get("_id")),
            "full_name": nama_pengirim,
            "profile_picture": foto_pengirim,
            "post_owner": d.get("post_owner", ""),
            "post_id": str(d.get("post_id", "")),
            "time": waktu,
            "is_read": d.get("is_read", False),
            "type": d.get("type", "tag")
        })

    return jsonify(list_notif), 200


    
@app.route("/post-profile", methods=["GET", "POST"])
@login_required
def post_profile():
    if request.method == "POST":
        text = request.form.get("text")
        image = request.files.get("image")
        
        image_path = "None"
        if image and image.filename != "":
            filename = secure_filename(image.filename)
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image.save(image_path)
            
        post_data = {
            "user_id": ObjectId(session["user_id"]),
            "full_name": session["full_name"],
            "email": session["email"],
            "text": text,
            "image": image_path,
            "timestamp": datetime.utcnow(),
            "reactions": {
                "like": [],
                "love": [],
                "haha": [],
                "sad": [],
                "angry": []
            }
        }
        mongo.db.posts.insert_one(post_data)
        return redirect(url_for("profile"))

    
@app.route("/react/<post_id>", methods=["POST"])
@login_required
def react(post_id):
    user_id = session["user_id"]
    data = request.get_json()
    reaction = data.get("reaction")

    valid_reactions = ["like", "love", "haha", "happy", "think", "lovely"]
    if reaction not in valid_reactions:
        return jsonify({"error": "Invalid reaction"}), 400

    post = mongo.db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        return jsonify({"error": "Post not found"}), 404

    # Remove user from all reactions
    updates = {}
    for r in valid_reactions:
        updates[f"reactions.{r}"] = post.get("reactions", {}).get(r, [])
        if user_id in updates[f"reactions.{r}"]:
            updates[f"reactions.{r}"].remove(user_id)

    # Add user to the new reaction
    updates[f"reactions.{reaction}"].append(user_id)

    # Update Data di MongoDB
    mongo.db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$set": {f"reactions.{r}": updates[f"reactions.{r}"] for r in valid_reactions}}
    )

    return jsonify({"message": "Reaction updated successfully!"})

@app.route("/edit-profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    user_id = ObjectId(session["user_id"])
    user_data = mongo.db.user.find_one({"_id": user_id})
    # Ambil daftar teman yang sudah accepted
    accepted_friends = list(mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": str(user_id), "status": "accepted"},
            {"receiver_id": str(user_id), "status": "accepted"}
        ]
    }))
    friends_ids = []
    for f in accepted_friends:
        friends_ids.append(f["receiver_id"] if f["sender_id"] == str(user_id) else f["sender_id"])
    friend_users = list(mongo.db.user.find({"_id": {"$in": [ObjectId(fid) for fid in friends_ids]}}))

    friend_requests = list(mongo.db.friend_requests.find({
        "receiver_id": user_id,
        "status": "pending"
    }))

    sender_ids = [ObjectId(req["sender_id"]) for req in friend_requests]
    senders = list(mongo.db.user.find({"_id": {"$in": sender_ids}}))
    senders_dict = {str(sender["_id"]): sender for sender in senders}

    # Gabungkan data pengirim ke dalam permintaan
    for req in friend_requests:
        req["sender_data"] = senders_dict.get(req["sender_id"])
        
    friend_list_data = [{
        "_id": str(f["_id"]),
        "full_name": f.get("full_name", ""),
        "profile_picture": f.get("profile_picture", "/static/default.png")
    } for f in friend_users]
    
    if request.method == "POST":
        full_name = request.form.get("full_name")
        lname = request.form.get("lname")
        email = request.form.get("email")
        mobile = request.form.get("mobile")
        avatar = request.files.get("avatar")  # TANPA koma
        gender = request.form.get("gender")
        dob = request.form.get("dob")
        country = request.form.get("country")
        languages = request.form.getlist("language[]")  # Multi-select
        interest = request.form.get("interest")
        website = request.form.get("website")
        social = request.form.get("social")
        address = request.form.get("address")
        
        if avatar and avatar.filename != "":
            filename = secure_filename(avatar.filename)
            avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            avatar.save(avatar_path)
        

        mongo.db.user.update_one(
            {"_id": ObjectId(session["user_id"])},
            {"$set": {
                "full_name": full_name,
                "last_name": lname,
                "profile_picture": avatar_path if avatar else None,
                "email": email,
                "phone": mobile,
                "gender": gender,
                "dob": dob,
                "country": country,
                "languages": languages,
                "interest": interest,
                "website": website,
                "social": social,
                "address": address
            }}
        )

        flash("Profil berhasil diperbarui!")
        return redirect(url_for("profile"))

    user_data = mongo.db.user.find_one({"_id": ObjectId(session["user_id"])})
    return render_template("profile/profile-edit.html", 
                           user=user_data,full_name=session.get("full_name"),
                           last_name=session.get("last_name"),
                            req=friend_requests, 
                            friend_list_data=friend_list_data,)

@app.route("/search",methods=["GET"])
@login_required
def search():
    user_id = session.get("user_id")
    current_user = mongo.db.user.find_one({"_id": ObjectId(user_id)})
    query = request.args.get("q", "").strip()
    current_user_id = str(session.get("user_id"))

    results = mongo.db.user.find({
        "full_name": {"$regex": query, "$options": "i"}
    })
    
    people = []
    for person in results:
        person_id = str(person["_id"])
        if person_id == current_user_id:
            continue
        
        friendship = mongo.db.friend_requests.find_one({
            "$or": [
                {"sender_id": current_user_id, "receiver_id": person_id},
                {"sender_id": person_id, "receiver_id": current_user_id}
            ]
        })
        
        status = None
        if friendship:
            status = friendship["status"] 
            
        people.append({
            "full_name": person["full_name"].strip(),
            "image": person.get("profile_picture", "/static/default.jpg"),
            "bio": person.get("interest", ""),
            "status" : status
        })

    return render_template("search/search.html", 
                           people=people,
                           full_name=session.get("full_name"),
                           email=session.get("email"),
                           user = current_user)

        
    
@app.route("/komentar/<post_id>", methods=["POST"])
@login_required
def kirim_komentar(post_id):
    komentar_text = request.form.get("komentar")
    user_id = session.get("user_id")
    user = mongo.db.user.find_one({"_id": ObjectId(user_id)})

    if user and komentar_text:
        komentar_data = {
            "post_id": ObjectId(post_id),  # Tambahkan ini
            "user_id": ObjectId(user_id),
            "full_name": user.get("full_name"),
            "email": user.get("email"),
            "avatar": user.get("avatar", "static/assets/images/user/01.jpg"),  # Tambahkan default kosong jika tidak ada
            "komentar": komentar_text,
            "timestamp": datetime.utcnow()
        }
        mongo.db.komentar.insert_one(komentar_data)

    return redirect(url_for("dashboard"))


@app.route("/komentar_profile/<post_id>", methods=["POST"])
@login_required
def komentar_profile(post_id):
    komentar_text = request.form.get("komentar")
    user_id = session.get("user_id")
    user = mongo.db.user.find_one({"_id": ObjectId(user_id)})

    if user and komentar_text:
        komentar_data = {
            "post_id": ObjectId(post_id),  # Tambahkan ini
            "user_id": ObjectId(user_id),
            "full_name": user.get("full_name"),
            "email": user.get("email"),
            "avatar": user.get("avatar", "static/assets/images/user/01.jpg"),  # Tambahkan default kosong jika tidak ada
            "komentar": komentar_text,
            "timestamp": datetime.utcnow()
        }
        mongo.db.komentar.insert_one(komentar_data)

    return redirect(url_for("profile"))

@app.route("/edit-password", methods=["GET", "POST"])
@login_required
def edit_password():
    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")
        
        user = mongo.db.user.find_one({"_id": ObjectId(session["user_id"])})
        if user and check_password_hash(user.get("password"), current_password):
            if new_password == confirm_password:
                hashed_pw = generate_password_hash(new_password)
                mongo.db.user.update_one(
                    {"_id" : ObjectId(session["user_id"])}, 
                    {"$set": {"password": hashed_pw}}
                )
                flash("Password berhasil diperbarui!")
                return redirect(url_for("profile"))
            else:
                flash("Password Baru Tidak Cocok!")
                return redirect(url_for("edit_password"))
        else:
            flash("Password saat ini salah.")
            return redirect(url_for("edit_password"))
        
@app.route("/group", methods=["GET"])
@login_required
def group():
    user_id = ObjectId(session["user_id"])
    user_data = mongo.db.user.find_one({"_id": user_id})
    # Ambil daftar teman yang sudah accepted
    accepted_friends = list(mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": str(user_id), "status": "accepted"},
            {"receiver_id": str(user_id), "status": "accepted"}
        ]
    }))
    friends_ids = []
    for f in accepted_friends:
        friends_ids.append(f["receiver_id"] if f["sender_id"] == str(user_id) else f["sender_id"])
    friend_users = list(mongo.db.user.find({"_id": {"$in": [ObjectId(fid) for fid in friends_ids]}}))

    friend_requests = list(mongo.db.friend_requests.find({
        "receiver_id": user_id,
        "status": "pending"
    }))

    sender_ids = [ObjectId(req["sender_id"]) for req in friend_requests]
    senders = list(mongo.db.user.find({"_id": {"$in": sender_ids}}))
    senders_dict = {str(sender["_id"]): sender for sender in senders}

    # Gabungkan data pengirim ke dalam permintaan
    for req in friend_requests:
        req["sender_data"] = senders_dict.get(req["sender_id"])
        
    friend_list_data = [{
        "_id": str(f["_id"]),
        "full_name": f.get("full_name", ""),
        "profile_picture": f.get("profile_picture", "/static/default.png")
    } for f in friend_users]
    
    return render_template("grup/group.html",
                            full_name=user_data.get("full_name"),
                            user=user_data,
                            req=friend_requests,  # <-- ini ditambahkan
                            friend_list_data=friend_list_data, )
    
@app.route("/group_detail", methods=["GET"])
@login_required
def group_detail():
    user_id = ObjectId(session["user_id"])
    user_data = mongo.db.user.find_one({"_id": user_id})
    # Ambil daftar teman yang sudah accepted
    accepted_friends = list(mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": str(user_id), "status": "accepted"},
            {"receiver_id": str(user_id), "status": "accepted"}
        ]
    }))
    friends_ids = []
    for f in accepted_friends:
        friends_ids.append(f["receiver_id"] if f["sender_id"] == str(user_id) else f["sender_id"])
    friend_users = list(mongo.db.user.find({"_id": {"$in": [ObjectId(fid) for fid in friends_ids]}}))

    friend_requests = list(mongo.db.friend_requests.find({
        "receiver_id": user_id,
        "status": "pending"
    }))

    sender_ids = [ObjectId(req["sender_id"]) for req in friend_requests]
    senders = list(mongo.db.user.find({"_id": {"$in": sender_ids}}))
    senders_dict = {str(sender["_id"]): sender for sender in senders}

    # Gabungkan data pengirim ke dalam permintaan
    for req in friend_requests:
        req["sender_data"] = senders_dict.get(req["sender_id"])
        
    friend_list_data = [{
        "_id": str(f["_id"]),
        "full_name": f.get("full_name", ""),
        "profile_picture": f.get("profile_picture", "/static/default.png")
    } for f in friend_users]
    
    return render_template("grup/group-detail.html",
                            full_name=user_data.get("full_name"),
                            user=user_data,
                            req=friend_requests,  # <-- ini ditambahkan
                            friend_list_data=friend_list_data, )

@app.route("/profile", methods=["GET"])
@login_required
def profile():
    user_id = ObjectId(session["user_id"])
    user_data = mongo.db.user.find_one({"_id": user_id})
    users = list(mongo.db.user.find())
    users_dict = {str(user['_id']): user['full_name'] for user in users}


    # Teman yang sudah diterima
    friends_requests = mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": str(user_id)},
            {"receiver_id": str(user_id)}
        ],
        "status": "accepted"
    })
    
    # Request yang masih pending
    friend_requests = list(mongo.db.friend_requests.find({
        "receiver_id": str(user_id),
        "status": "pending"
    }))
    
    friends_requests = list(friends_requests)
    friend_count = len(friends_requests)
    
     # Ambil daftar teman yang sudah accepted
    accepted_friends = list(mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": str(user_id), "status": "accepted"},
            {"receiver_id": str(user_id), "status": "accepted"}
        ]
    }))

    friends_ids = []
    for f in accepted_friends:
        friends_ids.append(f["receiver_id"] if f["sender_id"] == str(user_id) else f["sender_id"])
    friend_users = list(mongo.db.user.find({"_id": {"$in": [ObjectId(fid) for fid in friends_ids]}}))

        
    friend_list_data = [{
        "_id": str(f["_id"]),
        "full_name": f.get("full_name", ""),
        "profile_picture": f.get("profile_picture", "/static/default.png")
    } for f in friend_users]
    
    # Ambil data pengirim request
    sender_ids = [ObjectId(req["sender_id"]) for req in friend_requests]
    senders = list(mongo.db.user.find({"_id": {"$in": sender_ids}}))
    senders_dict = {str(sender["_id"]): sender for sender in senders}

    for req in friend_requests:
        req["sender_data"] = senders_dict.get(req["sender_id"])
    
    # Ambil daftar ID teman
    friend_ids = []
    for f in friends_requests:
        if f["sender_id"] == str(user_id):
            friend_ids.append(ObjectId(f["receiver_id"]))
        else:
            friend_ids.append(ObjectId(f["sender_id"]))

    # Ambil data user dari daftar teman
    friend_users = list(mongo.db.user.find({"_id": {"$in": friend_ids}}))

    # ➕ Tambahkan friend_count untuk setiap teman
    for friend in friend_users:
        friend_id = str(friend["_id"])
        friend_rels = mongo.db.friend_requests.find({
            "$or": [
                {"sender_id": friend_id},
                {"receiver_id": friend_id}
            ],
            "status": "accepted"
        })
        friend["friend_count"] = sum(1 for _ in friend_rels)
    
    # Ambil postingan user
    posts = list(mongo.db.posts.find({"user_id": user_id}).sort("timestamp", -1))
    for post in posts:
        post["like_count"] = sum(len(post["reactions"].get(reac, [])) for reac in post["reactions"])

        all_reacted_user_ids = set()
        for user_list in post.get("reactions", {}).values():
            all_reacted_user_ids.update(user_list)
        post["liked_users"] = [users_dict.get(uid, "Unknown") for uid in all_reacted_user_ids]

        komentar_list = list(mongo.db.komentar.find({"post_id": post["_id"]}).sort("timestamp", -1))
        for komentar in komentar_list:
            komentar["full_name"] = users_dict.get(str(komentar["user_id"]), "Anonim")
        post["komentar_list"] = komentar_list  # <- PENTING! assign komentar_list ke post
        post["komentar_count"] = len(komentar_list)

        user_reaction = None
        for reaction, uids in post["reactions"].items():
            if session["user_id"] in uids:
                user_reaction = reaction
                break
        post["user_reaction"] = user_reaction or "like"


    return render_template(
        "profile/profile.html",
        full_name=user_data.get("full_name"),
        user=user_data,
        friend_users=friend_users,
        friend_count = friend_count,
        email=user_data.get("email"),
        posts=posts,
        req=friend_requests,
        friend_list_data = friend_list_data,
    )
    
@app.route("/profile/<user_id>", methods=["GET"])
@login_required
def profile_user(user_id):
    # Ambil user yang sedang dikunjungi (profil yang dilihat)
    user = mongo.db.user.find_one({
        "full_name": {
            "$regex": f"^{user_id.replace('-', ' ')}$",
            "$options": "i"
        }
    })
    if not user:
        return "User Not Found", 404

    viewed_user_id = str(user["_id"])  # user yang dikunjungi
    current_user_id = str(session.get("user_id"))  # user yang sedang login
    

    # Cek apakah user yang sedang login berteman dengan user yang dikunjungi
    is_friend = mongo.db.friend_requests.find_one({
        "$or": [
            {"sender_id": current_user_id, "receiver_id": viewed_user_id},
            {"sender_id": viewed_user_id, "receiver_id": current_user_id}
        ],
        "status": "accepted"
    }) is not None

    # -------- Friend list milik user yang sedang LOGIN --------
    accepted_friends_login = list(mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": current_user_id, "status": "accepted"},
            {"receiver_id": current_user_id, "status": "accepted"}
        ]
    }))

    login_friend_ids = [
        f["receiver_id"] if f["sender_id"] == current_user_id else f["sender_id"]
        for f in accepted_friends_login
    ]

    login_friend_users = list(mongo.db.user.find({
        "_id": {"$in": [ObjectId(fid) for fid in login_friend_ids]}
    }))
    
    friend_requests = list(mongo.db.friend_requests.find({
        "receiver_id": str(current_user_id),
        "status": "pending"
    }))
    
    # Ambil data pengirim request
    sender_ids = [ObjectId(req["sender_id"]) for req in friend_requests]
    senders = list(mongo.db.user.find({"_id": {"$in": sender_ids}}))
    senders_dict = {str(sender["_id"]): sender for sender in senders}

    for req in friend_requests:
        sender = senders_dict.get(req["sender_id"])
        if sender:
            pic = sender.get("profile_picture", "")
            pic = pic.replace("\\", "/").replace("static/", "")
            image_path = pic if pic.startswith("uploads/") else f"uploads/{pic}"
            if not pic.strip():
                image_path = "static/default.png"

            sender["image_path"] = image_path
        req["sender_data"] = sender or {
            "full_name": "Unknown User",
            "image_path": "static/default.png"
    }

        
        
    friend_list_data = []
    for f in login_friend_users:
        pic = f.get("profile_picture", "")
        pic = pic.replace("\\", "/").replace("static/", "")
        image_path = pic if pic.startswith("uploads/") else f"uploads/{pic}"
        if not pic.strip():
            image_path = "static/default.png"

        friend_list_data.append({
            "_id": str(f["_id"]),
            "full_name": f.get("full_name", ""),
            "profile_picture": image_path  # sudah fix path-nya
        })

    friends_requests = mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": viewed_user_id},
            {"receiver_id": viewed_user_id}
        ],
        "status": "accepted"
    })

    other_friend_ids = []
    for f in friends_requests:
        if f["sender_id"] == viewed_user_id:
            other_friend_ids.append(ObjectId(f["receiver_id"]))
        else:
            other_friend_ids.append(ObjectId(f["sender_id"]))

    friend_users = list(mongo.db.user.find({"_id": {"$in": other_friend_ids}}))
    for friend in friend_users:
        pic = friend.get("profile_picture", "")
        pic = pic.replace("\\", "/").replace("static/", "")
        friend["image_path"] = pic if pic.startswith("uploads/") else f"uploads/{pic}"

        friend_id = str(friend["_id"])
        friend_rels = mongo.db.friend_requests.find({
            "$or": [
                {"sender_id": friend_id},
                {"receiver_id": friend_id}
            ],
            "status": "accepted"
        })
        friend["friend_count"] = sum(1 for _ in friend_rels)

    # Gambar user yang dilihat
    pic = user.get("profile_picture", "")
    pic = pic.replace("\\", "/").replace("static/", "")
    image_path = pic if pic.startswith("uploads/") else f"uploads/{pic}"
    if not pic.strip():
        image_path = "static/default.png"

    return render_template(
        'search/profile_detail.html',
        user=user,
        friend_users=friend_users,  # untuk tampilan teman si target profile
        image_path=image_path,      # gambar utama
        full_name=user.get("full_name", ""),
        is_friend=is_friend,
        friend_list_data=friend_list_data,
        req = friend_requests
    )
    
@app.route("/unfriend/<user_id>", methods=["POST"])
@login_required
def unfriend_user(user_id):
    if not request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return "Bad Request", 400

    target_user = mongo.db.user.find_one({
        "full_name": {
            "$regex": f"^{user_id.replace('-', ' ')}$",
            "$options": "i"
        }
    })

    if not target_user:
        return {"success": False, "message": "User not found."}, 404

    target_user_id = str(target_user["_id"])
    current_user_id = str(session.get("user_id"))

    result = mongo.db.friend_requests.delete_one({
        "$or": [
            {"sender_id": current_user_id, "receiver_id": target_user_id},
            {"sender_id": target_user_id, "receiver_id": current_user_id}
        ],
        "status": "accepted"
    })

    if result.deleted_count > 0:
        return {"success": True, "message": "Berhasil Di Hapus"}
    else:
        return {"success": False, "message": "Friendship not found."}, 400
    

@app.route('/add_friend/', methods=['POST'])
@login_required
def add_friend():
    sender_id   = session['user_id']
    receiver_id = request.form.get('receiver_id') or request.json.get('receiver_id')
    if not receiver_id:
        flash('No user selected.', 'danger')
        return redirect(request.referrer or url_for('dashboard'))

    success = send_request_friend(sender_id, receiver_id)
    if success:
        flash('Permintaan pertemanan dikirim.', 'success')
    else:
        flash('Permintaan sudah dikirim sebelumnya.', 'warning')
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/accept_friend/', methods=['POST'])
@login_required
def accept_friend():
    sender_id = request.form.get('sender_id')
    receiver_id = session['user_id']
    if not sender_id:
        flash('No user selected.', 'danger')
        return redirect(request.referrer or url_for('dashboard'))

    # perform the accept logic
    accept_request_friend(sender_id, receiver_id)
    flash('Friend request accepted!', 'success')
    return redirect(request.referrer or url_for('dashboard'))

@app.route("/send_message", methods=["POST"])
@login_required
def send_message():
    receiver_id = request.form.get("receiver_id")
    message = request.form.get("message")
    sender_id = request.form.get("sender_id")

    print("Sender ID:", sender_id)
    print("Receiver ID:", receiver_id)
    print("Message:", message)

    # Tambahkan debug ini di sini
    print("Online Users:", list(online_users.keys()))
    print("Receiver in room?", receiver_id in online_users)

    if not all([sender_id, receiver_id, message]):
        return "Missing fields", 400

    try:
        # Simpan ke database
        mongo.db.messages.insert_one({
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "message": message,
            "timestamp": datetime.utcnow()
        })

        full_name = online_users.get(sender_id, {}).get("full_name", session.get("full_name"))
        profile_picture = online_users.get(sender_id, {}).get("profile_picture", None)

        message_data = {
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "message": message,
            "full_name": full_name,
            "profile_picture": profile_picture,
            "timestamp": str(datetime.utcnow())
        }

        # Kirim pesan ke penerima dan pengirim (biar muncul langsung di UI)
        socketio.emit("new_message", message_data, room=receiver_id)
        socketio.emit("new_message", message_data, room=sender_id)

        return jsonify(success=True), 200

    except Exception as e:
        print("Error saat simpan pesan:", e)
        return "Internal Server Error", 500
    
@app.route("/messages/<other_user_id>", methods=["GET"])
@login_required
def get_message(other_user_id):
    current_id = session.get("user_id")
    if not current_id or not ObjectId.is_valid(current_id) or not ObjectId.is_valid(other_user_id):
        return jsonify([]),400
    
    user_obj = ObjectId(current_id)
    other_obj = ObjectId(other_user_id)
    
    cursor = mongo.db.messages.find({
        "$or": [
            { "sender_id":   user_obj,  "receiver_id": other_obj },
            { "sender_id":   other_obj, "receiver_id": user_obj }
        ]
    }).sort("timestamp", 1)
    
    result = []
    for m in cursor:
        result.append({
            "sender_id":   str(m["sender_id"]),
            "receiver_id": str(m["receiver_id"]),
            "message":     m["content"],
            "timestamp":   m["timestamp"].isoformat()
        })
    return jsonify(result), 200


@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    user_id = session.get("user_id")
    current_user = mongo.db.user.find_one({"_id": ObjectId(user_id)})

    # Ambil semua postingan
    
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
        
    offset = (page - 1) * POST_PER_PAGE
    limit = POST_PER_PAGE 
       
    posts = mongo.db.posts.find().sort("timestamp", -1).skip(offset).limit(limit)
    posts_with_reaction = []
    users = list(mongo.db.user.find())
    users_dict = {str(user['_id']): user['full_name'] for user in users}
    
    accepted_friends = list(mongo.db.friend_requests.find({
        "$or": [
            {"sender_id": user_id, "status": "accepted"},
            {"receiver_id": user_id, "status": "accepted"}
        ]
    }))
    
    friend_ids = []
    
    for f in accepted_friends:
        if f["sender_id"] == user_id:
            friend_ids.append(f["receiver_id"])
        else:
            friend_ids.append(f["sender_id"])
            
   
        
    

    # Tambahkan reaksi & komentar untuk setiap postingan
    for post in posts:
        
        pemilik = str(post.get("user_id"))
        privasi = post.get("privasi", "Publik")
        boleh_liat = False
        
        if privasi == "Publik":
            boleh_liat = True
        elif privasi == "Teman":
            if pemilik == user_id or pemilik in friend_ids:
                boleh_liat = True
        elif privasi == "Hanya Saya":
            if pemilik == user_id:
                boleh_liat = True
                
        if not boleh_liat:
            continue
       
        
        ## Fungsi Reaction
        post["user_reaction"] = get_user_reaction(post, user_id)
        all_reacted_user_ids = set()
        for user_list in post.get("reactions", {}).values():
            all_reacted_user_ids.update(user_list)
        post["like_count"] = len(all_reacted_user_ids)
        komentar_list = list(mongo.db.komentar.find({"post_id": post["_id"]}).sort("timestamp", -1))
        
        ## Fungsi Komentar
        for komentar in komentar_list:
            komentar["full_name"] = users_dict.get(str(komentar["user_id"]), "Anonim")
        post["komentar_list"] = komentar_list
        post["komentar_count"] = len(komentar_list)
        post["liked_users"] = [users_dict.get(uid, "Unknown") for uid in all_reacted_user_ids]
        
        posts_with_reaction.append(post)
    total_posts_count = mongo.db.posts.count_documents({})
        
    is_ajax = request.args.get("ajax") == "1"
    if is_ajax:
        return render_template("_post_items.html", posts=posts_with_reaction)


    friend_requests = list(mongo.db.friend_requests.find({
        "receiver_id": user_id,
        "status": "pending"
    }))

    pending_count = len(friend_requests)
    
    sender_ids = [ObjectId(req["sender_id"]) for req in friend_requests]
    senders = list(mongo.db.user.find({"_id": {"$in": sender_ids}}))
    senders_dict = {str(sender["_id"]): sender for sender in senders}

    # Gabungkan data pengirim ke dalam permintaan
    for req in friend_requests:
        req["sender_data"] = senders_dict.get(req["sender_id"])
        
     # Ambil daftar teman yang sudah accepted
    
    
            
    friend_users = list(mongo.db.user.find({
        "_id": {"$in": [ObjectId(fid) for fid in friend_ids]}
    }))
    
    friend_list_data = []
    for f in friend_users:
            # Ambil raw path
        pic = f.get("profile_picture", "").replace("\\", "/")

        # Hapus semua leading "static/" atau "/static/"
        pic = re.sub(r'^/*static/*', '', pic)

        # Jika kosong, pakai default
        if not pic.strip():
            pic = "default.png"

        # Pastikan pakai uploads/ kalau memang file di folder uploads
        if not pic.startswith("uploads/"):
            pic = f"uploads/{pic}"

        pic_url = url_for('static', filename=pic)

        friend_list_data.append({
            "_id": str(f["_id"]),
            "full_name": f.get("full_name", ""),
            "profile_picture": pic_url
        })
    

    return render_template(
        'index.html',
        full_name=session.get("full_name"),
        email=session.get("email"),
        posts=posts_with_reaction,
        current_page=page,          # pastikan ini ada
        posts_per_page=POST_PER_PAGE,
        req=friend_requests,  # <-- ini ditambahkan
        friend_list_data=friend_list_data,  # ✅ untuk sidebar/online users
        user=current_user,
        pending_count = pending_count,
        total_posts=total_posts_count,
    )


# Route: Logout
@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Anda telah logout.")
    return redirect(url_for("login"))

if __name__ == '__main__':
    socketio.run(app, debug=True)

