let localStream, peerConnection;
let incomingCallData = null;
let remoteDescSet = false;
let queuedCandidates = [];
let cameraOn = true;
let micOn = true;
let onlineIds = [];
let selectedTargetUser = null;

const currentUser = window.CURRENT_USER_ID; // e.g. dari <script>let CURRENT_USER_ID = "{{ user_id }}"</script>
const currentUserName = window.CURRENT_USER_NAME; // dari Flask juga

const config = {
  iceServers: [
    { urls: "stun:stun.relay.metered.ca:80" },
    {
      urls: "turn:global.relay.metered.ca:80",
      username: "739d178dfb590a9426f6cc65",
      credential: "DlrblmLJ4XFOqLnh",
    },
    {
      urls: "turn:global.relay.metered.ca:443",
      username: "739d178dfb590a9426f6cc65",
      credential: "DlrblmLJ4XFOqLnh",
    },
    {
      urls: "turns:global.relay.metered.ca:443?transport=tcp",
      username: "739d178dfb590a9426f6cc65",
      credential: "DlrblmLJ4XFOqLnh",
    },
  ],
};

async function setupPeer(toUser) {
  peerConnection = new RTCPeerConnection(config);

  localStream.getTracks().forEach((track) => {
    peerConnection.addTrack(track, localStream);
  });

  peerConnection.onicecandidate = (event) => {
    if (event.candidate) {
      socket.emit("ice-candidate", {
        to: toUser,
        candidate: event.candidate,
      });
    }
  };

  peerConnection.ontrack = (event) => {
    console.log("📽️ Remote stream received");
    document.getElementById("remoteVideo").srcObject = event.streams[0];
    document.getElementById("callingStatus").classList.add("d-none");
    document.getElementById("callControls").classList.remove("d-none");
  };

  peerConnection.oniceconnectionstatechange = () => {
    console.log("ICE State:", peerConnection.iceConnectionState);
  };

  peerConnection.onconnectionstatechange = () => {
    console.log("Peer Connection State:", peerConnection.connectionState);
  };
}

async function startCall() {
  const target = selectedTargetUser; // Harus user_id

  try {
    localStream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: true,
    });
    document.getElementById("localVideo").srcObject = localStream;

    await setupPeer(target);
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);

    socket.emit("call", {
      from: currentUser,
      to: target,
      offer: offer,
    });
    document.getElementById("chatbox6").style.display = "none";
    new bootstrap.Modal(document.getElementById("callScreen")).show();
    document.getElementById("remoteVideo").classList.add("d-none");
    document.getElementById("callControls").classList.remove("d-none");
    document.getElementById("callingStatus").classList.remove("d-none");

    showControlsTemporarily();
  } catch (err) {
    console.error("❌ Gagal memulai panggilan:", err);
    alert("Gagal mengakses kamera/mikrofon.");
  }
}

async function acceptIncoming() {
  const { fromUser, offer } = incomingCallData;

  document.getElementById("callOverlay").style.display = "none";
  localStream = await navigator.mediaDevices.getUserMedia({
    video: true,
    audio: true,
  });
  document.getElementById("localVideo").srcObject = localStream;

  await setupPeer(fromUser);
  await peerConnection.setRemoteDescription(new RTCSessionDescription(offer));
  remoteDescSet = true;

  for (let candidate of queuedCandidates) {
    await peerConnection.addIceCandidate(new RTCIceCandidate(candidate));
  }
  queuedCandidates = [];

  const answer = await peerConnection.createAnswer();
  await peerConnection.setLocalDescription(answer);

  socket.emit("answer", {
    to: fromUser,
    answer: answer,
  });

  new bootstrap.Modal(document.getElementById("callScreen")).show();
  document.getElementById("chatbox6").style.display = "none";
  document.getElementById("callerNameStatus").classList.add("d-none");
  document.getElementById("callControls").classList.remove("d-none");
  showControlsTemporarily();
}

function declineIncoming() {
  document.getElementById("callOverlay").style.display = "none";
  document.getElementById("incomingCallBox").style.display = "none";
  incomingCallData = null;
}

function endCall() {
  document.getElementById("chatbox6").style.display = "block";
  if (localStream) localStream.getTracks().forEach((track) => track.stop());
  if (peerConnection) {
    peerConnection.close();
    peerConnection = null;
  }
  remoteDescSet = false;
  queuedCandidates = [];
  bootstrap.Modal.getInstance(document.getElementById("callScreen"))?.hide();
}

// Mute dan Kamera
function stopCamera() {
  const localVideo = document.getElementById("localVideo");
  const cameraBtnIcon = document.querySelector("#cameraToggleBtn");
  const videoTrack = localStream?.getVideoTracks()[0];

  if (!cameraBtnIcon || !videoTrack) return;

  videoTrack.enabled = !videoTrack.enabled;
  cameraOn = videoTrack.enabled;
  cameraBtnIcon.classList.toggle("fa-video-slash", cameraOn);
  cameraBtnIcon.classList.toggle("fa-video", !cameraOn);
}

function Muted() {
  const audioBtnIcon = document.querySelector("#audioToggleBtn");
  const audioTrack = localStream?.getAudioTracks()[0];

  if (!audioBtnIcon || !audioTrack) return;

  audioTrack.enabled = !audioTrack.enabled;
  micOn = audioTrack.enabled;

  socket.emit("mic-toggle", {
    to: selectedTargetUser,
    micOn: micOn,
  });

  audioBtnIcon.classList.toggle("fa-microphone-slash", micOn);
  audioBtnIcon.classList.toggle("fa-microphone", !micOn);

  document
    .getElementById("micMutedBadgeLocal")
    .classList.toggle("d-none", micOn);
}

// Auto-hide control bar
let controlTimeout;
function showControlsTemporarily() {
  const controls = document.getElementById("callControls");
  controls.classList.remove("d-none");

  if (controlTimeout) clearTimeout(controlTimeout);
  controlTimeout = setTimeout(() => {
    controls.classList.add("d-none");
  }, 3000);
}

document
  .getElementById("callScreen")
  .addEventListener("mousemove", showControlsTemporarily);
