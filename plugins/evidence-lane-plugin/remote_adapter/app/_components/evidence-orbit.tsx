"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

export function EvidenceOrbit() {
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

    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.6));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    camera.position.set(0, 0.25, 8.2);

    const group = new THREE.Group();
    group.rotation.set(-0.42, 0.18, 0.08);
    scene.add(group);

    const ringSpecs = [
      { radius: 2.35, tube: 0.055, color: 0x2f7cf1, opacity: 0.56, z: -0.3 },
      { radius: 1.78, tube: 0.045, color: 0x31c5d9, opacity: 0.46, z: 0.12 },
      { radius: 1.2, tube: 0.04, color: 0xc88722, opacity: 0.52, z: 0.42 },
    ] as const;
    const geometries: THREE.BufferGeometry[] = [];
    const materials: THREE.Material[] = [];

    ringSpecs.forEach((spec, index) => {
      const geometry = new THREE.TorusGeometry(spec.radius, spec.tube, 20, 180);
      const material = new THREE.MeshPhysicalMaterial({
        color: spec.color,
        metalness: 0.12,
        roughness: 0.22,
        transmission: 0.42,
        transparent: true,
        opacity: spec.opacity,
        clearcoat: 1,
        clearcoatRoughness: 0.16,
      });
      const ring = new THREE.Mesh(geometry, material);
      ring.position.z = spec.z;
      ring.rotation.x = index === 1 ? 0.38 : index === 2 ? -0.32 : 0;
      ring.rotation.y = index === 1 ? -0.22 : index === 2 ? 0.28 : 0;
      group.add(ring);
      geometries.push(geometry);
      materials.push(material);
    });

    const glassGeometry = new THREE.CircleGeometry(1.58, 96);
    const glassMaterial = new THREE.MeshPhysicalMaterial({
      color: 0xeef5ff,
      transmission: 0.72,
      transparent: true,
      opacity: 0.15,
      roughness: 0.08,
      metalness: 0,
      side: THREE.DoubleSide,
    });
    const glass = new THREE.Mesh(glassGeometry, glassMaterial);
    glass.position.z = -0.48;
    group.add(glass);
    geometries.push(glassGeometry);
    materials.push(glassMaterial);

    scene.add(new THREE.AmbientLight(0xffffff, 2.1));
    const keyLight = new THREE.DirectionalLight(0xbcd9ff, 4.2);
    keyLight.position.set(3, 4, 6);
    scene.add(keyLight);
    const warmLight = new THREE.DirectionalLight(0xffd38b, 2.2);
    warmLight.position.set(-4, -2, 4);
    scene.add(warmLight);

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

    let targetX = group.rotation.x;
    let targetY = group.rotation.y;
    const handlePointer = (event: PointerEvent) => {
      const rect = host.getBoundingClientRect();
      targetY = ((event.clientX - rect.left) / rect.width - 0.5) * 0.56;
      targetX = -0.42 + ((event.clientY - rect.top) / rect.height - 0.5) * 0.24;
    };
    host.addEventListener("pointermove", handlePointer);

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;
    const animate = () => {
      group.rotation.x += (targetX - group.rotation.x) * 0.035;
      group.rotation.y += (targetY - group.rotation.y) * 0.035;
      group.rotation.z += 0.0008;
      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(animate);
    };
    if (!reducedMotion) animate();

    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      host.removeEventListener("pointermove", handlePointer);
      resizeObserver.disconnect();
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, []);

  return <div className="evidenceOrbit" ref={hostRef} aria-hidden="true" />;
}
