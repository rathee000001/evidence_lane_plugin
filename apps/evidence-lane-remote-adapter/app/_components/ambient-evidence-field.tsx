"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

export default function AmbientEvidenceField() {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    } catch {
      host.dataset.renderer = "unavailable";
      return;
    }

    renderer.setClearColor(0xffffff, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.35));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);
    host.dataset.renderer = "webgl";

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 80);
    camera.position.set(0, 0, 13);

    const field = new THREE.Group();
    field.rotation.set(-0.22, 0.08, 0.04);
    scene.add(field);

    const geometries: THREE.BufferGeometry[] = [];
    const materials: THREE.Material[] = [];
    const rings = [
      { radius: 4.7, tube: 0.016, color: 0x41cce7, opacity: 0.15, x: -2.7, y: 1.5 },
      { radius: 3.25, tube: 0.018, color: 0xe6b95d, opacity: 0.14, x: 3.8, y: -1.8 },
      { radius: 5.8, tube: 0.012, color: 0x5792f5, opacity: 0.09, x: 1.5, y: 2.8 },
    ] as const;

    rings.forEach((spec, index) => {
      const geometry = new THREE.TorusGeometry(spec.radius, spec.tube, 8, 140);
      const material = new THREE.MeshBasicMaterial({
        color: spec.color,
        transparent: true,
        opacity: spec.opacity,
      });
      const ring = new THREE.Mesh(geometry, material);
      ring.position.set(spec.x, spec.y, -2 - index * 0.6);
      ring.rotation.set(0.4 + index * 0.3, 0.2 - index * 0.22, index * 0.5);
      field.add(ring);
      geometries.push(geometry);
      materials.push(material);
    });

    const particleCount = 92;
    const positions = new Float32Array(particleCount * 3);
    for (let index = 0; index < particleCount; index += 1) {
      const offset = index * 3;
      const angle = index * 2.399963;
      const radius = 2.2 + ((index * 17) % 41) * 0.18;
      positions[offset] = Math.cos(angle) * radius;
      positions[offset + 1] = Math.sin(angle) * radius * 0.68;
      positions[offset + 2] = -1.5 - (index % 9) * 0.35;
    }
    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const particleMaterial = new THREE.PointsMaterial({
      color: 0x5ecbe8,
      transparent: true,
      opacity: 0.28,
      size: 0.045,
      sizeAttenuation: true,
    });
    const particles = new THREE.Points(particleGeometry, particleMaterial);
    field.add(particles);
    geometries.push(particleGeometry);
    materials.push(particleMaterial);

    const resize = () => {
      const width = Math.max(1, host.clientWidth);
      const height = Math.max(1, host.clientHeight);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(host);
    resize();

    let pointerX = 0;
    let pointerY = 0;
    const onPointerMove = (event: PointerEvent) => {
      pointerX = event.clientX / Math.max(window.innerWidth, 1) - 0.5;
      pointerY = event.clientY / Math.max(window.innerHeight, 1) - 0.5;
    };
    window.addEventListener("pointermove", onPointerMove, { passive: true });

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;
    const animate = () => {
      field.rotation.y += (pointerX * 0.08 - field.rotation.y) * 0.018;
      field.rotation.x += (-0.22 + pointerY * 0.04 - field.rotation.x) * 0.018;
      particles.rotation.z += 0.00045;
      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(animate);
    };
    if (!reducedMotion) animate();

    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onPointerMove);
      resizeObserver.disconnect();
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, []);

  return <div className="ambientEvidenceField" ref={hostRef} aria-hidden="true" />;
}
