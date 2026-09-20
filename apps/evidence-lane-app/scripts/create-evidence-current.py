"""Original Blender sculpture with a real Gather -> Flow morph for the Home story."""
import bpy,math
from pathlib import Path
from mathutils import Vector
APP=Path(__file__).resolve().parents[1];OUT=APP/'public/home-current';ART=APP/'artwork/home-current';OUT.mkdir(parents=True,exist_ok=True);ART.mkdir(parents=True,exist_ok=True)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
def material(name,c,metal=.4,rough=.23,emit=0):
 m=bpy.data.materials.new(name);m.use_nodes=True;m.diffuse_color=(*c,1);p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*c,1);p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=rough;p.inputs['Emission Color'].default_value=(*c,1);p.inputs['Emission Strength'].default_value=emit;return m
opal=material('Opal titanium',(.25,.48,.57),.72,.19)
silver=material('Porcelain silver',(.73,.85,.88),.46,.22)
dark=material('Midnight glass',(.012,.033,.047),.5,.18)
cyan=material('Electric cyan',(.035,.72,.95),.25,.22,2.8)
gold=material('Warm evidence',(.95,.57,.14),.3,.25,1.7)
def box(name,loc,scale,mat):
 bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.scale=scale;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(mat);b=o.modifiers.new('Precision bevel','BEVEL');b.width=.045;b.segments=3;o.modifiers.new('Normals','WEIGHTED_NORMAL');return o
def route(t,j,flow=False):
 a=t*math.tau
 if flow:return Vector(((t-.5)*11,(j-1)*.48,.24*math.sin(a)+(j-1)*.08))
 return Vector((3.1*math.sin(a),1.30*math.cos(a)+j*.19,1.7*math.sin(a*2+.2)+(j-1)*.45))
def ribbon(name,j,width,material,edge=0):
 N=220;W=6;verts=[];flow=[];faces=[]
 for i in range(N+1):
  t=i/N
  for k in range(W+1):
   d=(k/W-.5)*width+edge
   for mode,array in [(False,verts),(True,flow)]:
    center=route(t,j,mode);tangent=(route(min(1,t+.0002),j,mode)-route(max(0,t-.0002),j,mode)).normalized();normal=tangent.cross(Vector((0,0,1))).normalized();twist=(t*math.tau*1.3+j*.32) if not mode else .12*math.sin(t*math.tau)
    offset=(normal*math.cos(twist)+Vector((0,0,1))*math.sin(twist))*d
    array.append(tuple(center+offset))
 for i in range(N):
  for k in range(W):a=i*(W+1)+k;faces.append((a,a+1,a+W+2,a+W+1))
 mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update();o=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(o);o.data.materials.append(material)
 for p in mesh.polygons:p.use_smooth=True
 o.shape_key_add(name='Gather');key=o.shape_key_add(name='Flow')
 for v,p in zip(key.data,flow):v.co=p
 return o
for j in range(3):
 ribbon('Current_%d'%j,j,.52 if j==1 else .30,opal if j!=1 else silver)
 width=.52 if j==1 else .30
 for side in [-1,1]:ribbon('Current_edge_%d_%d'%(j,side),j,.014,cyan if j!=2 else gold,side*width/2)
for i in range(20):
 a=i*math.tau/20;r=3.8+(i%3)*.7
 parent=bpy.data.objects.new('Packet_%02d'%i,None);bpy.context.collection.objects.link(parent);parent.location=(math.cos(a)*r,math.sin(a)*r,math.sin(a*3)*.8);parent.rotation_euler=(.15*math.sin(a),.18*math.cos(a),a)
 o=box('Packet backing',(0,0,0),(.52,.72,.052),dark);o.parent=parent
 o=box('Packet surface',(0,0,.03),(.47,.65,.015),silver);o.parent=parent
 for k in range(3):o=box('Packet trace',(-.02,.17-k*.12,.045),(.29 if k<2 else .18,.016,.012),cyan if i%3 else gold);o.parent=parent
 o=box('Packet marker',(.16,.23,.047),(.043,.05,.015),cyan if i%3 else gold);o.parent=parent
for i in range(5):
 parent=bpy.data.objects.new('Stage_%d'%i,None);bpy.context.collection.objects.link(parent);parent.location=((i-2)*2.1,4,0)
 for x in [-.48,.48]:o=box('Stage edge',(x,0,.55),(.055,.07,1.15),silver);o.parent=parent
 for z in [0,1.12]:o=box('Stage edge',(0,0,z),(.98,.07,.055),silver);o.parent=parent
 o=box('Stage signal',(0,-.05,1.10),(.82,.02,.025),cyan);o.parent=parent
result=bpy.data.objects.new('Result',None);bpy.context.collection.objects.link(result);result.location=(0,7,.5)
for j in range(3):
 o=box('Result page',(j*.09,j*.10,j*.045),(1.7,2.2,.03),silver);o.parent=result
for i in range(7):o=box('Result trace',(-.15,.7-i*.23,.16),(1.10 if i<6 else .65,.025,.012),cyan if i%3 else gold);o.parent=result
scene=bpy.context.scene;scene.world.color=(.015,.025,.04)
for loc,color,power,size in [((0,-4,6),(.65,.9,1),1900,5),((-5,1,3),(.10,.68,1),2400,4),((4,4,3),(1,.65,.3),1400,4)]:
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.data.energy=power;o.data.color=color;o.data.size=size;o.rotation_euler=(-o.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(3,-8,6));camera=bpy.context.object;camera.rotation_euler=(-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=13;scene.camera=camera
scene.render.engine='CYCLES';scene.cycles.samples=40;scene.cycles.use_denoising=True;scene.render.film_transparent=True;scene.render.resolution_x=2200;scene.render.resolution_y=1500;scene.render.resolution_percentage=100;scene.view_settings.view_transform='AgX'
bpy.ops.wm.save_as_mainfile(filepath=str(ART/'evidence-current.blend'));bpy.ops.export_scene.gltf(filepath=str(OUT/'evidence-current.glb'),export_format='GLB',export_cameras=False,export_lights=False)
for name,morph in [('gather',0),('flow',1)]:
 for o in bpy.data.objects:
  if o.type=='MESH' and o.data.shape_keys:o.data.shape_keys.key_blocks['Flow'].value=morph
 for o in bpy.data.objects:
  if o.name.startswith('Stage_') or o.name=='Result':
   o.hide_render=True
   for child in o.children_recursive:child.hide_render=True
 scene.render.filepath=str(OUT/(name+'.png'));bpy.ops.render.render(write_still=True)
print('EVIDENCE_CURRENT_RENDERED')
