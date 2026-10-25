// FAMA-Band socket integration - transradial myoelectric prosthesis (research socket)
//
//   openscad -D 'part="inner_socket"'   -o stl/socket_inner_socket.stl socket.scad
//   openscad -D 'part="cartridge"'      -o stl/socket_cartridge.stl    socket.scad
//   openscad -D 'part="hub_carrier"'    -o stl/socket_hub_carrier.stl socket.scad
//   openscad -D 'part="frame_anterior"' -o stl/socket_frame_anterior.stl socket.scad   (and frame_posterior)
//   openscad -D 'part="service_door"'   -o stl/socket_service_door.stl socket.scad
//   openscad -D 'part="assembly"' --render -o renders/socket_assembly.png socket.scad
//
// Geometry is driven by residual-limb measurements. For a real fitting, the
// prosthetist replaces limb() by the rectified scan of the residual limb
// (import("limb_scan.stl")) and keeps everything else: electrode ports,
// hub carrier and frame are placed relative to the same axis.
//
// Axes: z = residual-limb axis (z = 0 distal end of the limb, z = limb_len at
// the posterior brim), x = medial-lateral, y = anterior-posterior (+y anterior).
// Angles around z: 0 deg = lateral, 90 deg = anterior.

part = "assembly";
$fn = 64;

// ---------------------------------------------------------------- residual limb
limb_len   = 150;     // distal end to posterior brim (mm)
circ_prox  = 260;     // circumference at the brim (mm)
circ_dist  = 200;     // circumference 20 mm above the distal end (mm)
ml_ap      = 1.15;    // ellipse ratio medial-lateral / anterior-posterior
cap_len    = 20;      // distal end (rounded) length
// ---------------------------------------------------------------- prosthesis
forearm_len = 255;    // elbow line to wrist unit, matched to the sound side
wrist_d     = 50;     // wrist-unit lamination ring diameter (quick-disconnect wrist)
wall        = 3.5;    // inner socket wall (PA12 SLS or PETG-CF)
frame_gap   = 12;     // inner socket to frame: hub stack = 6 standoff + 1.6 PCB + 3.2 parts = 10.8 mm
frame_wall  = 2.5;
// ---------------------------------------------------------------- electrodes
elec_z      = 105;                          // ring height (proximal third: muscle bellies)
elec_angles = [0, 45, 90, 135, 180, 225, 270, 315];   // pod 1 lateral (ECR/ECU region), then around
pod_pcb     = [16, 34];                     // FAMA-Band pod PCB
pod_protrude = 0.5;
boss_h      = 3.0;                          // flat seat above the curved outer wall
// ---------------------------------------------------------------- hub (rigid-flex)
hub_z0      = 30;                           // board y = 0 (antenna end) at this height
hub_phi0    = 200;                          // angle of the analog island's outer edge (deg)
islands     = [[0, 33], [43, 67], [77, 109]];  // board x ranges of the rigid islands A, B, C
hub_holes   = [[15, 43.3], [29.8, 3.2], [46, 42.8], [64, 42.8], [106.4, 2.6], [80.6, 21.0]];
hub_stand   = 6.0;                          // standoff: clears bottom-side connectors (4.25 mm)
hub_h       = 46;
// ---------------------------------------------------------------- battery
batt        = [40, 60, 6];                  // 1S 1500 mAh (604060), in the distal frame

// perimeter of an ellipse a = k*b (Ramanujan) -> semi-axes for a circumference
function semi(c) = let(k = ml_ap, b = c / (PI * (3 * (k + 1) - sqrt((3 * k + 1) * (k + 3))))) [k * b, b];
function circ_at(z) = circ_dist + (circ_prox - circ_dist) * (z - cap_len) / (limb_len - cap_len);
function ab(z, off = 0) = let(s = semi(circ_at(max(z, cap_len)))) [s[0] + off, s[1] + off];
function r_at(z, phi, off = 0) = let(e = ab(z, off)) (e[0] * e[1]) / sqrt(pow(e[1] * cos(phi), 2) + pow(e[0] * sin(phi), 2));

module slice(z, off, h = 0.01) {
    e = ab(z, off);
    translate([0, 0, z]) scale([e[0], e[1], 1]) cylinder(r = 1, h = h);
}

// residual limb (or its offset surface): elliptical frustum + rounded distal end
module limb(off = 0, top = limb_len + 30) {
    e = ab(cap_len, off);
    hull() {
        translate([0, 0, cap_len]) scale([e[0], e[1], cap_len + off]) sphere(r = 1);
        for (z = [cap_len, (cap_len + top) / 2, top]) slice(z, off);
    }
}

// ---------------------------------------------------------------- inner socket
module trim() {
    // anterior cubital relief (elbow flexion) and a posterior olecranon extension
    translate([-150, 0, limb_len - 25]) cube([300, 150, 100]);         // anterior half lowered by 25 mm
    translate([-150, -150, limb_len]) cube([300, 300, 100]);           // posterior brim
}

function u_dir(phi) = [cos(phi), sin(phi), 0];

module at_surface(z, phi, off) {
    // frame with x' along the outward normal (approximated radially), z' along the limb axis
    r = r_at(z, phi, off);
    translate([r * cos(phi), r * sin(phi), z]) rotate([0, 0, phi]) children();
}

module electrode_boss(z, phi) {
    at_surface(z, phi, wall) translate([-1.5, -(pod_pcb[0] + 9) / 2, -(pod_pcb[1] + 9) / 2])
        cube([boss_h + 1.5, pod_pcb[0] + 9, pod_pcb[1] + 9]);
}

module electrode_window(z, phi) {
    at_surface(z, phi, 0) translate([-2, -(pod_pcb[0] + 0.4) / 2, -(pod_pcb[1] + 0.4) / 2])
        cube([wall + boss_h + 6, pod_pcb[0] + 0.4, pod_pcb[1] + 0.4]);
    for (s = [-1, 1]) at_surface(z, phi, wall) translate([0, 0, s * (pod_pcb[1] / 2 + 2.6)])
        rotate([0, 90, 0]) cylinder(d = 1.7, h = 20, center = true);          // M2 screw pilots
}

module inner_socket() {
    difference() {
        union() {
            limb(wall);
            for (a = elec_angles) electrode_boss(elec_z, a);
        }
        limb(0, limb_len + 60);
        trim();
        for (a = elec_angles) electrode_window(elec_z, a);
        // pod-number dots above each port (orientation aid)
        for (i = [0:len(elec_angles) - 1]) for (k = [0:i])
            at_surface(elec_z + pod_pcb[1] / 2 + 7, elec_angles[i], wall) translate([0, (k - i / 2) * 2.2, 0])
                rotate([0, 90, 0]) cylinder(d = 1.2, h = 2, center = true);
        // distal air-release valve port (suction suspension option)
        translate([0, 0, -wall - 1]) cylinder(d = 6, h = wall + 3);
    }
}

// ---------------------------------------------------------------- electrode cartridge
// Holds one pod: PCB glued into the plug so the electrodes stand pod_protrude
// proud of the socket's inner surface; flange screws onto the boss (2 x M2 x 6).
plug_len = wall + boss_h + pod_protrude;     // seat -> PCB skin face
module cartridge() {
    fl = [pod_pcb[0] + 9, pod_pcb[1] + 9, 2.0];
    difference() {
        union() {
            translate([-fl[0] / 2, -fl[1] / 2, 0]) cube(fl);                          // flange on the seat
            translate([-pod_pcb[0] / 2, -pod_pcb[1] / 2, -plug_len + 1.6]) cube([pod_pcb[0], pod_pcb[1], plug_len - 1.6 + 0.01]);
            translate([-fl[0] / 2 + 2, -fl[1] / 2 + 2, fl[2]]) cube([fl[0] - 4, fl[1] - 4, 2.6]); // component cover
        }
        // component cavity above the PCB (OPA2376, resistors, JST GH 4.25 mm)
        translate([-pod_pcb[0] / 2 + 0.8, -pod_pcb[1] / 2 + 0.8, -plug_len + 1.6 - 0.01]) cube([pod_pcb[0] - 1.6, pod_pcb[1] - 1.6, plug_len + 3]);
        // cable exit
        translate([-pod_pcb[0] / 2 - 6, -4.5, -plug_len + 1.6]) cube([8, 9, 5]);
        for (s = [-1, 1]) translate([0, s * (pod_pcb[1] / 2 + 2.6), -1]) cylinder(d = 2.3, h = 10);
    }
    // the PCB itself sits in the plug mouth: z from -plug_len to -plug_len + 1.6
}

// ---------------------------------------------------------------- hub carrier
z_hub_mid = hub_z0 + hub_h / 2;
R_hub = (r_at(z_hub_mid, hub_phi0 + 50, wall)) + hub_stand;   // facet distance from the axis
function x2phi(x) = hub_phi0 + x / R_hub * 180 / PI;          // arc-length mapping board x -> angle

module island_frame(i) {
    // local frame of rigid island i: origin at its centre on the facet plane, y' along the board x
    xc = (islands[i][0] + islands[i][1]) / 2;
    rotate([0, 0, x2phi(xc)]) translate([R_hub, 0, hub_z0]) children();
}

function island_of(x) = x <= islands[0][1] ? 0 : (x <= islands[1][1] ? 1 : 2);

module hub_carrier() {
    difference() {
        union() {
            for (h = hub_holes) let(i = island_of(h[0]), xc = (islands[i][0] + islands[i][1]) / 2)
                island_frame(i) translate([-(R_hub), h[0] - xc, h[1]]) rotate([0, 90, 0]) cylinder(d = 5.5, h = R_hub);
            // web that bonds to the socket: thin band under each island
            for (i = [0:2]) island_frame(i) translate([-R_hub, -(islands[i][1] - islands[i][0]) / 2 + 2, 6])
                cube([R_hub - 0.01, islands[i][1] - islands[i][0] - 4, 3]);
        }
        limb(wall - 0.2, limb_len + 60);                    // follows the socket's outer surface
        for (h = hub_holes) let(i = island_of(h[0]), xc = (islands[i][0] + islands[i][1]) / 2)
            island_frame(i) translate([-8, h[0] - xc, h[1]]) rotate([0, 90, 0]) cylinder(d = 1.7, h = 9);  // M2 pilot
        for (i = [0:2]) island_frame(i) translate([0, -100, -10]) cube([50, 200, 100]);   // flat facet plane
    }
}

// simplified rigid-flex board on the carrier (renders only)
module hub_board() {
    for (i = [0:2]) island_frame(i) {
        w = islands[i][1] - islands[i][0];
        color("darkgreen") translate([0, -w / 2, 0]) cube([1.6, w, hub_h]);
        if (i == 0) color("black") translate([1.6, -w / 2 + 14.5, 21]) cube([1.2, 12, 12]);
        if (i == 1) color("silver") translate([1.6, -9, -6]) cube([3.1, 18, 25.5]);
        if (i == 2) color("silver") translate([1.6, -w / 2 + 2, 37.4]) cube([3.2, 10, 9]);
    }
    // flex hinges: polyimide arcs between the islands
    for (k = [0:1]) let(x0 = islands[k][1], x1 = islands[k + 1][0])
        color([0.85, 0.55, 0.1]) for (s = [0:5]) let(x = x0 + (x1 - x0) * s / 5)
            rotate([0, 0, x2phi(x)]) translate([R_hub - 0.2, -0.6, hub_z0 + 6]) cube([0.2, 1.4, 34]);
}

// ---------------------------------------------------------------- outer frame (forearm)
frame_top = limb_len - 12;
z_wrist = limb_len - forearm_len;
module frame_solid(off) {
    hull() {
        for (z = [cap_len + 5, (cap_len + frame_top) / 2, frame_top]) slice(z, wall + frame_gap + off);
        translate([0, 0, z_wrist + 12]) cylinder(d = wrist_d + 6 + 2 * off, h = 0.01);
    }
    translate([0, 0, z_wrist]) cylinder(d = wrist_d + 6 + 2 * off, h = 12.01);
}

module frame() {
    difference() {
        frame_solid(0);
        difference() { frame_solid(-frame_wall); translate([0, 0, z_wrist - 1]) cylinder(d = wrist_d + 7, h = 12); }
        translate([0, 0, z_wrist - 1]) cylinder(d = wrist_d, h = 14);           // wrist-unit lamination ring bore
        translate([0, 0, frame_top - 0.01]) cylinder(r = 200, h = 50);         // open towards the brim
        // service door over island C (USB-C, microSD, power switch, charge LED)
        island_frame(2) translate([0, -20, -6]) cube([40, 40, 58]);
    }
    // battery bay ribs in the distal frame
    translate([-batt[0] / 2 - 2, -batt[2] / 2 - 2 - 18, z_wrist + 30]) difference() {
        cube([batt[0] + 4, batt[2] + 4, batt[1] + 4]);
        translate([2, 2, 2]) cube([batt[0], batt[2], batt[1] + 5]);
    }
}

module frame_half(sign) {
    // clamshell split in the medial-lateral plane; joined by 6 x M3 and bonded to the socket at the brim
    intersection() { frame(); translate([-200, sign > 0 ? 0 : -400, z_wrist - 10]) cube([400, 400, 600]); }
}

module service_door() {
    intersection() {
        difference() { frame_solid(0); frame_solid(-frame_wall); }
        island_frame(2) translate([0.5, -19.5, -5.5]) cube([39, 39, 57]);
    }
}

// ---------------------------------------------------------------- views
module assembly(explode = 0) {
    color([0.95, 0.8, 0.7]) limb(0, limb_len - 2);                         // residual limb
    color("whitesmoke") inner_socket();
    for (i = [0:len(elec_angles) - 1]) at_surface(elec_z, elec_angles[i], wall + boss_h) translate([explode, 0, 0])
        rotate([0, 90, 0]) rotate([0, 0, 90]) color("lightgray") cartridge();
    color("gray") hub_carrier();
    translate([0, 0, 0]) hub_board();
    color("royalblue") translate([-batt[0] / 2, -batt[2] / 2 - 18, z_wrist + 32]) cube([batt[0], batt[2], batt[1]]);
    color([0.75, 0.8, 0.9, 0.35]) translate([0, explode * 3, 0]) frame_half(1);
    color([0.75, 0.8, 0.9, 0.35]) translate([0, -explode * 3, 0]) frame_half(-1);
    color("dimgray") translate([0, 0, z_wrist - 25]) cylinder(d = wrist_d - 2, h = 25);   // wrist unit (stand-in)
}

if (part == "inner_socket") inner_socket();
if (part == "cartridge") rotate([180, 0, 0]) cartridge();
if (part == "hub_carrier") hub_carrier();
if (part == "frame_anterior") frame_half(1);
if (part == "frame_posterior") frame_half(-1);
if (part == "service_door") service_door();
if (part == "assembly") assembly(0);
if (part == "exploded") assembly(12);
if (part == "cutaway") difference() { assembly(0); translate([-200, 0, -300]) cube([400, 400, 800]); }
if (part == "socket_only") { inner_socket(); hub_carrier(); hub_board(); }
