"""Original Home world: folded cerebral sculpture, glass vessel, evidence leaves and work path."""
import bpy,math,random
from pathlib import Path
from mathutils import Vector
APP=Path(__file__).resolve().parents[1];OUT=APP/'public/home-world';ART=APP/'artwork/home-world';OUT.mkdir(parents=True,exist_ok=True);ART.mkdir(parents=True,exist_ok=True)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
random.seed(92)
def material(name,c,metal=0,rough=.3,emission=0):
 m=bpy.data.materials.new(name);m.use_nodes=True;m.diffuse_color=(*c,1);p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*c,1);p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=rough;p.inputs['Emission Color'].default_value=(*c,1);p.inputs['Emission Strength'].default_value=emission;return m
porcelain=material('Satin neural porcelain',(.63,.78,.78),.12,.3)
ceramic=material('Obsidian instrument',(.022,.058,.069),.65,.2)
pearl=material('Brushed platinum',(.58,.70,.73),.7,.23)
cyan=material('Ion cyan',(.03,.69,.8),.3,.2,2.3)
gold=material('Warm signal',(.92,.52,.13),.4,.22,1.4)
violet=material('Context violet',(.38,.22,.85),.35,.25,1.7)
def box(name,loc,scale,mat,bevel=.04):
 bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.scale=scale;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(mat);b=o.modifiers.new('Radiused edge','BEVEL');b.width=bevel;b.segments=4;o.modifiers.new('Corner normals','WEIGHTED_NORMAL');return o
def tube(name,coords,r,mat):
 c=bpy.data.curves.new(name,'CURVE');c.dimensions='3D';c.bevel_depth=r;c.bevel_resolution=3;s=c.splines.new('POLY');s.points.add(len(coords)-1)
 for p,co in zip(s.points,coords):p.co=(*co,1)
 o=bpy.data.objects.new(name,c);bpy.context.collection.objects.link(o);o.data.materials.append(mat);return o
def torus(name,loc,major,minor,mat):
 bpy.ops.mesh.primitive_torus_add(major_radius=major,minor_radius=minor,major_segments=128,minor_segments=12,location=loc);o=bpy.context.object;o.name=name;o.data.materials.append(mat);return o
brain=bpy.data.objects.new('Brain',None);bpy.context.collection.objects.link(brain)
# A compact bilobed cortical sculpture with irregular connected gyri and a visible central fissure.
for side in [-1,1]:
 verts=[];faces=[];U=144;V=112
 for j in range(V+1):
  theta=math.pi*j/V
  for i in range(U):
   phi=math.tau*i/U
   # Broad anatomical silhouette with smaller sculptural folds; illustrative, not a medical model.
   ridges=math.sin(16*phi+3.5*math.sin(theta*6)+1.7*math.sin(phi*3+theta*4))
   ridges+=.62*math.sin(theta*21+2.9*math.sin(phi*5)+math.cos(theta*8-phi*3))
   fold=.044*ridges*(math.sin(theta)**.6)
   radius=1+fold
   x=side*(.49+.48*math.sin(theta)*math.cos(phi)*radius)
   y=.96*math.sin(theta)*math.sin(phi)*radius
   z=.77*math.cos(theta)*radius+.14
   verts.append((x,y,z))
 for j in range(V):
  for i in range(U):
   a=j*U+i;b=j*U+(i+1)%U;faces.append((a,b,b+U,a+U))
 mesh=bpy.data.meshes.new('Folded hemisphere');mesh.from_pydata(verts,[],faces);mesh.update();o=bpy.data.objects.new('Cortex_left' if side<0 else 'Cortex_right',mesh);bpy.context.collection.objects.link(o);o.parent=brain;o.data.materials.append(porcelain)
 for poly in mesh.polygons:poly.use_smooth=True
 # Cortical paths are embedded as narrow luminous threads, rather than an exterior wire cage.
 for j in range(5):
  coords=[]
  for k in range(65):
   theta=.35+k/64*2.25;phi=.3+j*.52+.08*math.sin(theta*7+j)
   x=side*(.49+.50*math.sin(theta)*math.cos(phi));y=.98*math.sin(theta)*math.sin(phi);z=.79*math.cos(theta)+.14
   coords.append((x,y,z))
  line=tube('Neural_trace',coords,.006,cyan if j%2 else gold);line.parent=brain
brain.rotation_euler=(.13,0,.18)
# A polished vessel and its floating equatorial instrument band.
glass=material('Glass vessel',(.48,.85,.9),0,.06);p=glass.node_tree.nodes.get('Principled BSDF');p.inputs['Transmission Weight'].default_value=1;p.inputs['IOR'].default_value=1.13
bpy.ops.mesh.primitive_uv_sphere_add(segments=80,ring_count=48,radius=1.49,location=(0,0,.1));orb=bpy.context.object;orb.name='Orb_shell';orb.data.materials.append(glass)
for poly in orb.data.polygons:poly.use_smooth=True
torus('Vessel rim',(0,0,-.52),1.37,.018,pearl)
torus('Vessel signal',(0,0,-.50),1.39,.007,cyan)
for z,r in [(-1.31,.56),(-1.40,.44)]:
 bpy.ops.mesh.primitive_cylinder_add(vertices=80,radius=r,depth=.06,location=(0,0,z));o=bpy.context.object;o.name='Core pedestal';o.data.materials.append(ceramic)
torus('Base pulse',(0,0,-1.34),.56,.012,cyan)
# Evidence leaves are real beveled objects, with a separate parent for story animation.
for i in range(18):
 a=i*math.tau/18;radius=2.4+(i%3)*.64;z=math.sin(a*2)*.44
 root=bpy.data.objects.new('Evidence_%02d'%i,None);bpy.context.collection.objects.link(root);root.location=(math.cos(a)*radius,math.sin(a)*radius,z);root.rotation_euler=(.12*math.cos(a),.16*math.sin(a),a+.3)
 o=box('Leaf body',(0,0,0),(.62,.85,.07),ceramic,.06);o.parent=root
 o=box('Leaf face',(0,0,.042),(.55,.76,.012),pearl,.035);o.parent=root
 accent=[cyan,gold,violet][i%3]
 for k in range(4):
  o=box('Evidence line',(-.045,.22-k*.12,.053),(.36 if k<3 else .20,.018,.009),accent,.006);o.parent=root
 o=box('Evidence seal',(.19,.28,.057),(.052,.055,.018),accent,.008);o.parent=root
# Swooping meridian ribbons avoid a flat solar-system silhouette.
for j in range(3):
 coords=[]
 for k in range(180):
  t=k/179*math.tau;r=2.25+j*.6
  coords.append((math.cos(t)*r,math.sin(t)*r,.52*math.sin(t*2+j)+j*.11))
 tube('Meridian_%d'%j,coords,.006,cyan if j!=1 else gold)
# Six physical work gates: their release positions are controlled by the browser story.
for i in range(5):
 root=bpy.data.objects.new('Gate_%d'%i,None);bpy.context.collection.objects.link(root);root.location=((i-2)*1.55,5,-.8)
 for x in [-.46,.46]:o=box('Gate upright',(x,0,.55),(.07,.12,1.2),pearl);o.parent=root
 for z in [-.02,1.12]:o=box('Gate lintel',(0,0,z),(.98,.12,.07),pearl);o.parent=root
 o=box('Gate light',(0,-.073,1.10),(.79,.024,.02),cyan,.008);o.parent=root
 o=box('Gate foot',(0,0,-.08),(1.1,.62,.09),ceramic);o.parent=root
scene=bpy.context.scene;scene.world.color=(.02,.035,.05)
for name,loc,color,power,size in [('Key',(1,-4,6),(.7,.92,1),1500,5),('Rim',(-4,1,3),(.18,.7,1),2000,4),('Warm',(3,3,2),(1,.65,.28),1100,3)]:
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.name=name;o.data.energy=power;o.data.color=color;o.data.size=size;o.rotation_euler=(Vector((0,0,0))-o.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(4,-7,5));cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,.1))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=10.5;scene.camera=cam
scene.render.engine='CYCLES';scene.cycles.samples=48;scene.cycles.use_denoising=True;scene.render.film_transparent=True;scene.render.resolution_x=2000;scene.render.resolution_y=1500;scene.render.resolution_percentage=100;scene.view_settings.view_transform='AgX'
bpy.ops.wm.save_as_mainfile(filepath=str(ART/'context-vessel.blend'))
bpy.ops.export_scene.gltf(filepath=str(OUT/'context-vessel.glb'),export_format='GLB',export_cameras=False,export_lights=False)
scene.render.filepath=str(OUT/'context-vessel.png');bpy.ops.render.render(write_still=True)
print('HOME_WORLD_RENDERED')
