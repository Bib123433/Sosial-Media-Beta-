from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from datetime import datetime
import os
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from humanize import naturaltime
from flask_mail import Mail, Message
import random, string
import re


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

@app.template_filter('naturaltime')
def naturaltime_filter(value):
    import humanize
    import datetime
    now = datetime.datetime.utcnow()
    return humanize.naturaltime(now - value)

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
        return redirect(url_for("dashboard"))
    
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
    return render_template("profile/profile-edit.html", user=user_data,full_name=session.get("full_name"),last_name=session.get("last_name")
)

@app.route("/search",methods=["GET"])
@login_required
def search():
    query = request.args.get("q", "").strip()
    results = mongo.db.user.find({
        "full_name": {"$regex": query, "$options": "i"}
    })

    people = []
    for person in results:
        people.append({
            "full_name": person["full_name"].strip(),
            "image": person.get("profile_picture", "/static/default.jpg"),
            "bio": person.get("interest", "")
        })

    return render_template("search/search.html", people=people, full_name=session.get("full_name"),
)

        
    
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

@app.route("/profile", methods=["GET"])
@login_required
def profile():
    user_id = ObjectId(session["user_id"])
    user_data = mongo.db.user.find_one({"_id": user_id})
    
    friends_cursor = mongo.db.friends.find({
    "$or": [
        {"user_id": user_id},
        {"friend_id": user_id}
    ]
})
    
    friend_ids = []
    
    for f in friends_cursor:
        if f["user_id"] == user_id:
            friend_ids.append(f["friend_id"])
        else:
            friend_ids.append(f["user_id"])
    
    posts = list(mongo.db.posts.find({"user_id": user_id}).sort("timestamp", -1))
    friend_users = list(mongo.db.user.find({"_id": {"$in": [ObjectId(fid) for fid in friend_ids]}}))

    for post in posts:
        post["like_count"] = sum(len(post["reactions"].get(reac, [])) for reac in post["reactions"])

        liked_user_ids = post["reactions"].get("like", [])
        liked_users = mongo.db.user.find({"_id": {"$in": [ObjectId(uid) for uid in liked_user_ids]}})
        post["liked_users"] = [user["full_name"] for user in liked_users]
        komentar_list = list(mongo.db.komentar.find({"post_id": post["_id"]}).sort("timestamp", -1))
        post["komentar_list"] = komentar_list
        post["komentar_count"] = len(komentar_list)
        
        # Untuk icon default (misalnya pakai 'like' kalau belum ada)
        user_reaction = None
        for reaction, uids in post["reactions"].items():
            if session["user_id"] in uids:
                user_reaction = reaction
                break
        post["user_reaction"] = user_reaction or "like"

        # Komentar dummy jika belum ada
        post.setdefault("komentar_list", [])

    return render_template(
        "profile/profile.html",
        full_name=user_data.get("full_name"),
        user=user_data,
        friend_users=friend_users,
        email=user_data.get("email"),
        posts=posts  # <-- ini harus 'posts', bukan 'post'
    )
    
@app.route("/profile/<user_id>", methods=["GET"])
@login_required
def profile_user(user_id):
    user = mongo.db.user.find_one({
        "full_name": {
            "$regex": f"^{user_id.replace('-', ' ')}$",
            "$options": "i"
        }
    })
    if not user:
        return "User Not Found", 404

    pic = user.get("profile_picture", "")       
    pic = pic.replace("\\", "/").replace("static/", "")
    image_path = pic if pic.startswith("uploads/") else f"uploads/{pic}"

    # 3) Render template
    return render_template(
        'search/profile_detail.html',
        user=user,
        image_path=image_path,
        full_name=user.get("full_name", "")
    )

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


@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    user_id = session.get("user_id")
    
    # Ambil semua postingan
    posts = mongo.db.posts.find()
    posts_with_reaction = []
    users = list(mongo.db.user.find())
    users_dict = {str(user['_id']): user['full_name'] for user in users}

    # Tambahkan reaksi & komentar untuk setiap postingan
    for post in posts:
        post["user_reaction"] = get_user_reaction(post, user_id)
        all_reacted_user_ids = set()
        for user_list in post.get("reactions", {}).values():
            all_reacted_user_ids.update(user_list)
        post["like_count"] = len(all_reacted_user_ids)
        komentar_list = list(mongo.db.komentar.find({"post_id": post["_id"]}).sort("timestamp", -1))
        post["komentar_list"] = komentar_list
        post["komentar_count"] = len(komentar_list)
        post["liked_users"] = [users_dict.get(uid, "Unknown") for uid in all_reacted_user_ids]
        posts_with_reaction.append(post)

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

    return render_template(
        'index.html',
        full_name=session.get("full_name"),
        email=session.get("email"),
        posts=posts_with_reaction,
        req=friend_requests  # <-- ini ditambahkan
    )


# Route: Logout
@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Anda telah logout.")
    return redirect(url_for("login"))

if __name__ == '__main__':
    app.run(debug=True)
