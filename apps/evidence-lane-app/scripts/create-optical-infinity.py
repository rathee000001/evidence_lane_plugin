"""Original optical ribbon geometry for the user-approved Home direction."""
import bpy, math
from pathlib import Path
from mathutils import Vector
root=Path(__file__).resolve().parents[1]
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
def material(name,c,emission=0):
 m=bpy.data.materials.new(name);m.diffuse_color=(*c,1);m.use_nodes=True
 p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*c,1);p.inputs['Metallic'].default_value=.3;p.inputs['Roughness'].default_value=.12;p.inputs['Emission Color'].default_value=(*c,1);p.inputs['Emission Strength'].default_value=emission
 return m
glass=material('Optical glass',(.32,.57,.83)); cyan=material('Cyan edge',(.24,.77,1),2);violet=material('Violet edge',(.57,.39,1),1.8);gold=material('Warm thread',(.95,.68,.35),1.5)
def point(t,j,d=0):
 a=t*math.tau;center=Vector((4.25*math.cos(a),.75*math.sin(a),1.62*math.sin(2*a)))
 tangent=Vector((-4.25*math.sin(a),.75*math.cos(a),3.24*math.cos(2*a))).normalized()
 normal=tangent.cross(Vector((0,-1,0))).normalized();twist=.55*math.sin(a*2+j*.7)
 offset=normal*math.cos(twist)+Vector((0,1,0))*math.sin(twist)
 return center+offset*(d+(j-1)*.15)+Vector((0,(j-1)*.055,0))
for j in range(3):
 verts=[];faces=[];N=420;W=5;w=.21 if j==1 else .115
 for i in range(N+1):
  for k in range(W+1):verts.append(tuple(point(i/N,j,(k/W-.5)*w*(.40+.60*math.sin(i/N*math.tau*3+j*.9)**2))))
 for i in range(N):
  for k in range(W):a=i*(W+1)+k;faces.append((a,a+1,a+W+2,a+W+1))
 mesh=bpy.data.meshes.new('Optical ribbon');mesh.from_pydata(verts,[],faces);mesh.update();o=bpy.data.objects.new('OpticalRibbon_%s'%j,mesh);bpy.context.collection.objects.link(o);o.data.materials.append(glass)
 for f in mesh.polygons:f.use_smooth=True
 o.shape_key_add(name='Infinity')
 for name in ['Entry','Gather','Work','Evidence','Records','Studio']:
  key=o.shape_key_add(name=name)
  for index,v in enumerate(key.data):
   t=(index//(W+1))/N;a=t*math.tau;offset=Vector(verts[index])-point(t,j,0)
   if name=='Entry':center=Vector((4.1*math.cos(a),.9*math.sin(a),1.25*math.sin(2*a)))
   elif name=='Records':center=Vector((3.45*math.cos(a),.7*math.sin(2*a),2.45*math.sin(a)))
   elif name=='Studio':center=Vector((4.15*math.copysign(math.sqrt(abs(math.cos(a))),math.cos(a)),.65*math.sin(2*a),1.8*math.copysign(math.sqrt(abs(math.sin(a))),math.sin(a))))
   elif name=='Gather':center=Vector((4.55*math.cos(a),1.2*math.sin(a),2.1*math.sin(2*a)))
   elif name=='Work':center=Vector(((t-.5)*9,(j-1)*.38+.45*math.sin(a),.4*math.sin(a*1.5)+(j-1)*.35))
   else:center=Vector((4.3*math.cos(a),.8*math.sin(a),1.6*math.sin(2*a)*(1.2 if math.cos(a)>0 else .48)))
   v.co=center+offset

 for side in [-1,1]:
  c=bpy.data.curves.new('Optical edge','CURVE');c.dimensions='3D';c.bevel_depth=.009;c.bevel_resolution=3;s=c.splines.new('POLY');s.points.add(N)
  for i,v in enumerate(s.points):v.co=(*point(i/N,j,side*w/2),1)
  o=bpy.data.objects.new('Edge_%s_%s'%(j,side),c);bpy.context.collection.objects.link(o);o.data.materials.append([cyan,violet,gold][j])
out=root/'public/home-current';art=root/'artwork/home-current'
bpy.ops.wm.save_as_mainfile(filepath=str(art/'optical-infinity.blend'))
bpy.ops.export_scene.gltf(filepath=str(out/'optical-infinity.glb'),export_format='GLB',export_cameras=False,export_lights=False)
print('OPTICAL_INFINITY_EXPORTED')
