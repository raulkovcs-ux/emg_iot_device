// FAMA-Band rev A - parametric enclosures and band assembly
//
//   openscad -D 'part="pod"'      -o stl/pod_housing.stl famaband.scad
//   openscad -D 'part="hub_base"' -o stl/hub_base.stl    famaband.scad
//   openscad -D 'part="hub_lid"'  -o stl/hub_lid.stl     famaband.scad
//   openscad -D 'part="assembly"' -o renders/assembly.png famaband.scad
//
// Coordinates of PCB features are taken from the KiCad boards
// (hardware/kicad/*): PCB origin = top-left corner, x right, y towards the
// wrist. In this file y is mirrored (y_scad = -y_pcb) so the top view
// matches the PCB top view.
//
// Print: PETG or a skin-safe resin (e.g. medical/biocompatible grades),
// 0.2 mm layers, 3 perimeters. Pod: print upside down (roof on the bed).

part = "assembly";      // pod | hub_base | hub_lid | assembly | exploded | pcb_pod | pcb_hub
$fn = 48;

// ------------------------------------------------------------ common
wall = 1.6;
clr = 0.3;               // fit clearance around PCBs
pcb_t = 1.6;
strap_w = 25;            // elastic strap width
strap_t = 1.8;           // strap thickness

// ------------------------------------------------------------ pod
pod_pcb = [16, 34];
pod_comp_h = 4.4;        // tallest part: JST GH horizontal (4.25 mm)
pod_protrude = 0.5;      // electrodes stand proud of the housing rim
pod_wall = 1.2;
pod_roof = 1.2;
pod_tunnel_h = strap_t + 0.6;
pod_bridge = 1.4;
pod_outer = [pod_pcb[0] + 2 * (pod_wall + clr), pod_pcb[1] + 2 * (pod_wall + clr)];
pod_inner_h = pcb_t + pod_comp_h;               // from PCB bottom to roof
pod_body_h = pod_inner_h - pod_protrude + pod_roof;

module rounded_box(size, r) {
    hull() for (x = [r, size[0] - r], y = [r, size[1] - r]) translate([x, y, 0]) cylinder(r = r, h = size[2]);
}

module pod_housing(ch = 1) {
    ox = pod_outer[0]; oy = pod_outer[1];
    difference() {
        union() {
            rounded_box([ox, oy, pod_body_h], 2);
            // strap bridge: strap runs along x (around the forearm) through the tunnel
            translate([0, (oy - strap_w - 2 * 1.6) / 2, pod_body_h])
                difference() {
                    cube([ox, strap_w + 3.2, pod_tunnel_h + pod_bridge]);
                    translate([-1, 1.6, 0]) cube([ox + 2, strap_w, pod_tunnel_h]);
                }
        }
        // PCB pocket, open at the bottom (skin side)
        translate([pod_wall, pod_wall, -1]) cube([pod_pcb[0] + 2 * clr, pod_pcb[1] + 2 * clr, pod_inner_h - pod_protrude + 1]);
        // cable notch for the JST GH plug (connector at left edge, centre of the long side)
        translate([-1, oy / 2 - 4.5, pcb_t - pod_protrude]) cube([pod_wall + 2, 9, 4.6]);
        // glue pockets in the corners (UV resin / CA fixes the PCB)
        for (x = [pod_wall - 0.6, ox - pod_wall - 0.4], y = [pod_wall - 0.6, oy - pod_wall - 0.4])
            translate([x, y, -1]) cube([1, 1, 3]);
        // channel number on the bridge top
        translate([ox / 2, oy / 2, pod_body_h + pod_tunnel_h + pod_bridge - 0.6])
            linear_extrude(1) text(str(ch), size = 6, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
        // polarity marks (+ electrode at the top)
        translate([ox - 2.2, oy - 6, pod_body_h - 0.5]) linear_extrude(1) text("+", size = 3, halign = "center");
    }
}

// ------------------------------------------------------------ hub
hub_pcb = [66, 46];
ant_over = 6.0;              // ESP32-S3 module antenna beyond the PCB edge
batt = [40, 30, 6.2];        // LP603040 600 mAh (with protection PCM)
batt_pos = [12, 4];          // PCB coordinates of the cell's corner (skin side)
hub_floor = 1.4;
hub_lid_t = 1.4;
below_pcb = batt[2] + 0.6;   // battery + foam
above_pcb = 4.6;             // tallest top-side part (JST GH 4.25 mm, USB-C 3.3 mm)
hub_in = [hub_pcb[0] + ant_over + 2 * clr + 0.5, hub_pcb[1] + 2 * clr + 0.6];
hub_out = [hub_in[0] + 2 * wall, hub_in[1] + 2 * wall];
pcb_z = hub_floor + below_pcb;                 // PCB bottom
split_z = pcb_z + pcb_t;                       // base/lid split at PCB top
hub_h = split_z + above_pcb + hub_lid_t;
holes = [[15.0, 43.3], [31.0, 2.6], [42.0, 2.6]];  // PCB mounting holes (M2)

function P(p) = [wall + clr + p[0], hub_out[1] - wall - clr - p[1]];  // PCB -> scad

module hub_shell() {
    difference() {
        rounded_box([hub_out[0], hub_out[1], hub_h], 3);
        translate([wall, wall, hub_floor]) cube([hub_in[0], hub_in[1], hub_h - hub_lid_t - hub_floor]);
        // ---- left wall: the eight electrode cables (connectors on both PCB sides)
        translate([-1, P([0, 44.5])[1], pcb_z - 4.8]) cube([wall + 2, 43.5, pcb_t + 4.8 + 4.6]);
        // ---- wrist-side wall (PCB y = 46): USB-C, microSD, power switch, bias lead
        translate([P([45.6, 0])[0], -1, split_z + 0.1]) cube([10.0, wall + 2, 3.6]);      // USB-C plug
        translate([P([30.5, 0])[0], -1, split_z - 0.2]) cube([13.0, wall + 2, 2.6]);      // microSD card
        translate([P([57.5, 0])[0], -1, split_z]) cube([7.0, wall + 2, 2.4]);             // slide switch
        translate([P([5.8, 0])[0], -1, split_z]) cube([6.6, wall + 2, 4.6]);              // bias electrode plug
    }
}

module standoff(h, d = 4.6, hole = 1.7) {
    difference() { cylinder(d = d, h = h); translate([0, 0, 1]) cylinder(d = hole, h = h); }
}

module strap_loop() {
    // a bridge under the floor. The hub's long axis lies along the forearm, so
    // the strap (around the forearm) crosses the hub along y, under its centre.
    w = strap_w + 3.2; t = strap_t + 0.6;
    difference() {
        translate([-w / 2, 0, -t - 1.4]) cube([w, 5, t + 1.4]);
        translate([-strap_w / 2, -1, -t]) cube([strap_w, 7, t + 0.01]);
    }
}

module hub_base() {
    difference() {
        intersection() { hub_shell(); translate([-1, -1, -1]) cube([200, 200, split_z + 1]); }
        // battery pocket in the floor (0.6 mm deeper for foam tape)
        translate([P(batt_pos)[0], P(batt_pos)[1] - batt[1], hub_floor - 0.6]) cube([batt[0], batt[1], 2]);
    }
    for (h = holes) translate([P(h)[0], P(h)[1], hub_floor - 0.01]) standoff(pcb_z - hub_floor);
    // PCB support ledges along the long walls
    for (y = [wall, hub_out[1] - wall - 1.2]) translate([wall + 8, y, hub_floor]) cube([hub_pcb[0] - 16, 1.2, below_pcb - 0.05]);
    for (y = [6, hub_out[1] - 11]) translate([hub_out[0] / 2, y, 0]) strap_loop();
}

module hub_lid() {
    difference() {
        union() {
            difference() {
                intersection() { hub_shell(); translate([-1, -1, split_z]) cube([200, 200, 50]); }
            }
            for (h = holes) translate([P(h)[0], P(h)[1], split_z + 0.05]) cylinder(d = 4.6, h = above_pcb);
        }
        for (h = holes) translate([P(h)[0], P(h)[1], split_z - 1]) {
            cylinder(d = 2.3, h = 20);                                  // M2 clearance
            translate([0, 0, above_pcb + hub_lid_t - 1.4 + 1]) cylinder(d1 = 2.3, d2 = 4.2, h = 1.41);  // countersink
        }
        // buttons (RESET, BOOT/USER) - press with a 2 mm pin
        for (p = [[36.4, 3.2], [36.4, 8.2]]) translate([P(p)[0], P(p)[1], hub_h - 5]) cylinder(d = 2.4, h = 10);
        // status LEDs (green, blue, charge red) - light holes
        for (p = [[33.0, 12.8], [33.0, 14.6], [64.4, 31.4]]) translate([P(p)[0], P(p)[1], hub_h - 5]) cylinder(d = 1.6, h = 10);
        // labels
        translate([hub_out[0] / 2 + 4, hub_out[1] / 2 - 3, hub_h - 0.5]) linear_extrude(1)
            text("FAMA-Band", size = 6, halign = "center", font = "Liberation Sans:style=Bold");
        translate([5, hub_out[1] / 2, hub_h - 0.5]) linear_extrude(1) text("1-8", size = 3.5, valign = "center");
        translate([hub_out[0] / 2 + 4, 6, hub_h - 0.5]) linear_extrude(1)
            text("NOT WHILE CHARGING", size = 2.6, halign = "center");
    }
}

// ------------------------------------------------------------ simplified PCBs (renders only)
module pcb_pod() {
    color("darkgreen") cube([pod_pcb[0], pod_pcb[1], pcb_t]);
    color("gold") for (y = [7, 27]) translate([8, pod_pcb[1] - y, -0.05]) cylinder(d = 10, h = 0.1);
    color("dimgray") translate([0, 17 - 4.75, pcb_t]) cube([6.4, 9.5, 4.25]);
    color("black") translate([9, 15.5, pcb_t]) cube([3, 3, 1.1]);
}

module pcb_hub() {
    color("darkgreen") cube([hub_pcb[0], hub_pcb[1], pcb_t]);
    color("silver") translate([46.5, hub_pcb[1] - 22, pcb_t]) cube([25.5, 18, 3.1]);            // ESP32-S3 module
    color("black") translate([14.5, hub_pcb[1] - 25.5, pcb_t]) cube([12, 12, 1.2]);              // ADS1298
    color("silver") translate([46.1, -0.8, pcb_t]) cube([9, 7.5, 3.2]);                          // USB-C
    color("silver") translate([30.5, -0.6, pcb_t]) cube([12.8, 15, 2.0]);                        // microSD
    for (y = [6.0, 15.6, 25.2, 34.8]) color("white") translate([0, hub_pcb[1] - y - 4.75, pcb_t]) cube([6.4, 9.5, 4.25]);
    for (y = [9.8, 19.4, 29.0, 38.6]) color("white") translate([0, hub_pcb[1] - y - 4.75, -4.25]) cube([6.4, 9.5, 4.25]);
    color("royalblue") translate([batt_pos[0], hub_pcb[1] - batt_pos[1] - batt[1], -batt[2] - 0.3]) cube(batt);
}

// ------------------------------------------------------------ assembly on a forearm
forearm_a = 34;  // half-axes of the forearm cross-section at 1/3 elbow-wrist (circumference ~ 24 cm)
forearm_b = 28;

module forearm() {
    color([0.93, 0.78, 0.67]) rotate([0, 90, 0]) scale([forearm_b / forearm_a, 1, 1])
        cylinder(r1 = forearm_a + 4, r2 = forearm_a - 9, h = 230, center = true, $fn = 96);
}

function ell(t) = [forearm_a * cos(t), forearm_b * sin(t)];

module band(explode = 0) {
    // 8 pods equally spaced in angle around the ellipse; ch1 dorsal-radial
    for (i = [0:7]) {
        t = 90 - i * 45;
        p = ell(t);
        n = atan2(p[1] / (forearm_b * forearm_b), p[0] / (forearm_a * forearm_a));
        translate([0, p[0], p[1]]) rotate([n - 90, 0, 0]) translate([0, 0, explode]) {
            translate([-pod_outer[1] / 2, -pod_outer[0] / 2, 0]) rotate([0, 0, 0])
                translate([pod_outer[1], 0, 0]) rotate([0, 0, 90]) {
                    color("lightgray") pod_housing(i + 1);
                    translate([pod_wall + clr, pod_wall + clr, -pod_protrude]) pcb_pod();
                }
        }
    }
    // elastic strap
    color("dimgray") rotate([0, 90, 0]) scale([1, (forearm_b + 11) / (forearm_a + 11), 1])
        difference() { cylinder(r = forearm_a + 11, h = strap_w, center = true, $fn = 96); cylinder(r = forearm_a + 9, h = strap_w + 1, center = true, $fn = 96); }
    // hub rides on the outside of the strap over the dorsal pods
    // hub long axis along the forearm (x), strap under its centre
    translate([-hub_out[0] / 2, -hub_out[1] / 2, forearm_b + 15.5 + explode * 2]) {
            color("whitesmoke") hub_base();
            translate([wall + clr, wall + clr, pcb_z]) pcb_hub();
            color([0.8, 0.85, 0.9, 0.55]) hub_lid();
        }
}

if (part == "pod") pod_housing(1);
if (part == "hub_base") hub_base();
if (part == "hub_lid") translate([0, 0, hub_h]) rotate([180, 0, 0]) translate([0, 0, 0]) hub_lid();
if (part == "pcb_pod") pcb_pod();
if (part == "pcb_hub") pcb_hub();
if (part == "hub_exploded") { hub_base(); translate([wall + clr, wall + clr, pcb_z]) pcb_hub(); translate([0, 0, 18]) hub_lid(); }
if (part == "pod_exploded") { color("lightgray") pod_housing(1); translate([pod_wall + clr, pod_wall + clr, -pod_protrude - 12]) pcb_pod(); }
if (part == "assembly") { forearm(); band(0); }
if (part == "exploded") { forearm(); band(10); }
