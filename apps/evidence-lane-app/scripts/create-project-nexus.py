"""Original project instrument for the interactive source constellation. No brain or infinity motif."""
import bpy,math
from pathlib import Path
from mathutils import Vector
APP=Path(__file__).resolve().parents[1];OUT=APP/'public/home-current';ART=APP/'artwork/home-current'
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
def mat(name,c,metal=.6,rough=.2,emit=0):
 m=bpy.data.materials.new(name);m.use_nodes=True;m.diffuse_color=(*c,1);p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*c,1);p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=rough;p.inputs['Emission Color'].default_value=(*c,1);p.inputs['Emission Strength'].default_value=emit;return m
silver=mat('Pearlescent titanium',(.35,.57,.65),.8);dark=mat('Deep instrument enamel',(.015,.042,.064),.55);white=mat('Etched ceramic',(.67,.83,.88),.4);cyan=mat('Project cyan',(.035,.7,.94),.2,.23,2.7);amber=mat('Evidence amber',(.9,.5,.10),.3,.23,2)
def box(name,loc,scale,m):
 bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.scale=scale;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(m);b=o.modifiers.new('Machined bevel','BEVEL');b.width=.04;b.segments=4;o.modifiers.new('Normals','WEIGHTED_NORMAL');return o
def ring(name,loc,r,thickness,m):
 bpy.ops.mesh.primitive_torus_add(major_radius=r,minor_radius=thickness,major_segments=128,minor_segments=12,location=loc);o=bpy.context.object;o.name=name;o.data.materials.append(m);return o
def disk(name,z,r,d,m):
 bpy.ops.mesh.primitive_cylinder_add(vertices=96,radius=r,depth=d,location=(0,0,z));o=bpy.context.object;o.name=name;o.data.materials.append(m);b=o.modifiers.new('Rim bevel','BEVEL');b.width=.035;b.segments=3;o.modifiers.new('Normals','WEIGHTED_NORMAL');return o
disk('Project lower deck',-.72,1.30,.13,dark);disk('Project upper deck',-.49,1.21,.07,silver)
ring('Deck signal',(0,0,-.60),1.28,.025,cyan);ring('Outer instrument',(0,0,-.41),1.47,.035,silver);ring('Outer data path',(0,0,-.38),1.49,.01,amber)
for i in range(24):
 a=i*math.tau/24;x,y=1.08*math.cos(a),1.08*math.sin(a)
 o=box('Instrument tooth',(x,y,-.37),(.12,.26,.10),white);o.rotation_euler.z=a
 o=box('Status filament',(x,y,-.30),(.035,.14,.015),cyan if i%3 else amber);o.rotation_euler.z=a
for i in range(8):
 a=i*math.tau/8;x,y=.90*math.cos(a),.90*math.sin(a)
 o=box('Floating strut',(x,y,-.07),(.10,.10,.56),silver);o.rotation_euler.z=a
ring('Upper suspended rim',(0,0,.22),.93,.027,silver)
ring('Upper energy rim',(0,0,.24),.94,.008,cyan)
# The project's physical marker: an open, stepped evidence cube with luminous internal planes.
cube=box('Project cube',(0,0,.46),(.93,.93,.93),dark);cube.rotation_euler.z=math.pi/4
for z in [.17,.43,.69]:
 o=box('Evidence plane',(0,0,z),(1.02,1.02,.07),white);o.rotation_euler.z=math.pi/4
 o=box('Cyan plane',(0,0,z+.039),(1.025,1.025,.012),cyan);o.rotation_euler.z=math.pi/4
for i in range(4):
 a=math.pi/4+i*math.pi/2;x,y=.61*math.cos(a),.61*math.sin(a);box('Cube corner',(x,y,.46),(.075,.075,.98),silver)
ring('Project focus',(0,0,1.06),.33,.015,amber)
for i in range(12):
 a=i*math.tau/12;x,y=1.66*math.cos(a),1.66*math.sin(a);o=box('Evidence satellite',(x,y,-.23),(.18,.26,.08),dark);o.rotation_euler.z=a;box('Satellite light',(x,y,-.178),(.08,.13,.02),cyan)
scene=bpy.context.scene;scene.world.color=(.025,.04,.06)
for loc,color,power,size in [((2,-4,6),(.7,.9,1),1700,5),((-4,2,4),(.15,.7,1),1900,4),((4,3,2),(1,.6,.25),900,3)]:
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.data.energy=power;o.data.color=color;o.data.size=size;o.rotation_euler=(-o.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(4,-7,5));camera=bpy.context.object;camera.rotation_euler=(Vector((0,0,.15))-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=5;scene.camera=camera
scene.render.engine='CYCLES';scene.cycles.samples=40;scene.cycles.use_denoising=True;scene.render.film_transparent=True;scene.render.resolution_x=1600;scene.render.resolution_y=1600;scene.render.resolution_percentage=100;scene.view_settings.view_transform='AgX'
bpy.ops.wm.save_as_mainfile(filepath=str(ART/'project-nexus.blend'));bpy.ops.export_scene.gltf(filepath=str(OUT/'project-nexus.glb'),export_format='GLB',export_cameras=False,export_lights=False);scene.render.filepath=str(OUT/'project-nexus.png');bpy.ops.render.render(write_still=True)
