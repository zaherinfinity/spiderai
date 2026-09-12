/**
 * Live 3D background — floating particles + soft nebula (Three.js r128)
 * 2026 aesthetic for AI Translate Video
 */
(function () {
  const canvasHost = document.getElementById("bg-3d");
  if (!canvasHost || typeof THREE === "undefined") return;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
  camera.position.z = 28;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x000000, 0);
  canvasHost.appendChild(renderer.domElement);

  // Particles
  const COUNT = 900;
  const positions = new Float32Array(COUNT * 3);
  const colors = new Float32Array(COUNT * 3);
  const sizes = new Float32Array(COUNT);

  const colorA = new THREE.Color(0x4f6bff);
  const colorB = new THREE.Color(0xa855f7);
  const colorC = new THREE.Color(0x22d3ee);

  for (let i = 0; i < COUNT; i++) {
    const i3 = i * 3;
    positions[i3] = (Math.random() - 0.5) * 60;
    positions[i3 + 1] = (Math.random() - 0.5) * 40;
    positions[i3 + 2] = (Math.random() - 0.5) * 40;

    const t = Math.random();
    const c = t < 0.33 ? colorA : t < 0.66 ? colorB : colorC;
    colors[i3] = c.r;
    colors[i3 + 1] = c.g;
    colors[i3 + 2] = c.b;
    sizes[i] = Math.random() * 2.2 + 0.4;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geo.setAttribute("size", new THREE.BufferAttribute(sizes, 1));

  const mat = new THREE.PointsMaterial({
    size: 0.12,
    vertexColors: true,
    transparent: true,
    opacity: 0.85,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  // Soft glowing orbs
  const orbs = [];
  const orbColors = [0x4f6bff, 0xa855f7, 0x22d3ee];
  for (let i = 0; i < 5; i++) {
    const g = new THREE.SphereGeometry(1.8 + Math.random() * 2.5, 32, 32);
    const m = new THREE.MeshBasicMaterial({
      color: orbColors[i % 3],
      transparent: true,
      opacity: 0.06,
    });
    const mesh = new THREE.Mesh(g, m);
    mesh.position.set(
      (Math.random() - 0.5) * 30,
      (Math.random() - 0.5) * 18,
      (Math.random() - 0.5) * 20 - 5
    );
    mesh.userData = {
      speed: 0.001 + Math.random() * 0.002,
      phase: Math.random() * Math.PI * 2,
    };
    scene.add(mesh);
    orbs.push(mesh);
  }

  // Ambient light ring
  const ringGeo = new THREE.TorusGeometry(12, 0.08, 16, 100);
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0x6366f1,
    transparent: true,
    opacity: 0.15,
  });
  const ring = new THREE.Mesh(ringGeo, ringMat);
  ring.rotation.x = Math.PI / 2.4;
  scene.add(ring);

  let mouseX = 0, mouseY = 0;
  document.addEventListener("mousemove", (e) => {
    mouseX = (e.clientX / window.innerWidth - 0.5) * 4;
    mouseY = (e.clientY / window.innerHeight - 0.5) * 2;
  });

  function animate() {
    requestAnimationFrame(animate);
    const t = performance.now() * 0.001;

    points.rotation.y = t * 0.04;
    points.rotation.x = Math.sin(t * 0.2) * 0.08;

    orbs.forEach((o, i) => {
      o.position.y += Math.sin(t * o.userData.speed * 40 + o.userData.phase) * 0.008;
      o.position.x += Math.cos(t * o.userData.speed * 30 + i) * 0.004;
      o.scale.setScalar(1 + Math.sin(t * 0.8 + i) * 0.12);
    });

    ring.rotation.z = t * 0.15;
    ring.rotation.x = Math.PI / 2.4 + Math.sin(t * 0.3) * 0.1;

    camera.position.x += (mouseX - camera.position.x) * 0.03;
    camera.position.y += (-mouseY - camera.position.y) * 0.03;
    camera.lookAt(0, 0, 0);

    renderer.render(scene, camera);
  }
  animate();

  window.addEventListener("resize", () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });
})();
