"""
Build school image manifest from official school websites (.edu / athletics).

Pipeline:
  1. Resolve each school's official website (heuristic + HTTP verification)
  2. Fetch a limited set of targeted pages (homepage, admissions, student-life,
     athletics, swim) — max 8 pages per school, all cached to disk
  3. Extract image candidates from HTML
  4. Score each candidate per role (hero / student_life / swim)
  5. De-duplicate within school and across all schools
  6. Write static/school_images.json  (served to frontend)
     and data/school_images_debug.json (debug detail)

Usage:
  python3 scripts/build_school_images.py                    # fill gaps
  python3 scripts/build_school_images.py --rebuild          # force all
  python3 scripts/build_school_images.py --school "Kenyon College"
  python3 scripts/build_school_images.py --sample 5         # first 5 schools
"""

import sys, os, json, time, hashlib, re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from collections import Counter, defaultdict

import requests
from bs4 import BeautifulSoup

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR   = SCRIPT_DIR.parent
MANIFEST   = ROOT_DIR / 'static' / 'data' / 'school_images.json'
DATA_DIR   = ROOT_DIR / 'data'
DEBUG_OUT  = DATA_DIR / 'school_images_debug.json'
SITES_FILE = DATA_DIR / 'school_websites.json'
PAGE_CACHE = DATA_DIR / 'page_cache'

DATA_DIR.mkdir(exist_ok=True)
PAGE_CACHE.mkdir(exist_ok=True)

UA      = ('Mozilla/5.0 (compatible; Lane4Recruit/1.0; '
           '+https://lane4.app; contact@lane4.app)')
TIMEOUT = 8      # seconds per HTTP request
MAX_PAGES = 8    # hard cap per school

# ── known .edu overrides ──────────────────────────────────────────────────────
# Schools whose URLs can't be derived reliably from their name alone.
WEBSITE_OVERRIDES = {
    "MIT":                                           "https://web.mit.edu",
    "Caltech":                                       "https://www.caltech.edu",
    "Carnegie Mellon University":                    "https://www.cmu.edu",
    "Carnegie Mellon":                               "https://www.cmu.edu",
    "Georgia Institute of Technology":               "https://www.gatech.edu",
    "Georgia Tech":                                  "https://www.gatech.edu",
    "University of Michigan":                        "https://umich.edu",
    "University of California, Berkeley":            "https://www.berkeley.edu",
    "UC Berkeley":                                   "https://www.berkeley.edu",
    "University of Chicago":                         "https://www.uchicago.edu",
    "University of Virginia":                        "https://www.virginia.edu",
    "University of North Carolina at Chapel Hill":   "https://www.unc.edu",
    "UNC Chapel Hill":                               "https://www.unc.edu",
    "University of Connecticut":                     "https://uconn.edu",
    "University of Rhode Island":                    "https://www.uri.edu",
    "University of Vermont":                         "https://www.uvm.edu",
    "University of Massachusetts Amherst":           "https://www.umass.edu",
    "University of Massachusetts":                   "https://www.umass.edu",
    "University of New Hampshire":                   "https://www.unh.edu",
    "University of Maine":                           "https://umaine.edu",
    "University of Pittsburgh":                      "https://www.pitt.edu",
    "University of Southern California":             "https://www.usc.edu",
    "University of California San Diego":            "https://ucsd.edu",
    "UC San Diego":                                  "https://ucsd.edu",
    "University of California Davis":                "https://www.ucdavis.edu",
    "UC Davis":                                      "https://www.ucdavis.edu",
    "University of California Santa Barbara":        "https://www.ucsb.edu",
    "UC Santa Barbara":                              "https://www.ucsb.edu",
    "University of California Irvine":               "https://www.uci.edu",
    "UC Irvine":                                     "https://www.uci.edu",
    "University of California Los Angeles":          "https://www.ucla.edu",
    "UCLA":                                          "https://www.ucla.edu",
    "New York University":                           "https://www.nyu.edu",
    "NYU":                                           "https://www.nyu.edu",
    "Rensselaer Polytechnic Institute":              "https://www.rpi.edu",
    "Worcester Polytechnic Institute":               "https://www.wpi.edu",
    "Rochester Institute of Technology":             "https://www.rit.edu",
    "Case Western Reserve University":               "https://case.edu",
    "Washington University in St. Louis":            "https://wustl.edu",
    "Texas A&M University":                          "https://www.tamu.edu",
    "Texas A&M":                                     "https://www.tamu.edu",
    "College of William & Mary":                     "https://www.wm.edu",
    "William & Mary":                                "https://www.wm.edu",
    "United States Military Academy":                "https://www.westpoint.edu",
    "Army":                                          "https://www.westpoint.edu",
    "West Point":                                    "https://www.westpoint.edu",
    "United States Naval Academy":                   "https://www.usna.edu",
    "Navy":                                          "https://www.usna.edu",
    "United States Air Force Academy":               "https://www.usafa.edu",
    "Air Force":                                     "https://www.usafa.edu",
    "SUNY Binghamton":                               "https://www.binghamton.edu",
    "Binghamton University":                         "https://www.binghamton.edu",
    "SUNY Buffalo":                                  "https://www.buffalo.edu",
    "University at Buffalo":                         "https://www.buffalo.edu",
    "Penn State":                                    "https://www.psu.edu",
    "Pennsylvania State University":                 "https://www.psu.edu",
    "Ohio State University":                         "https://www.osu.edu",
    "Purdue University":                             "https://www.purdue.edu",
    "Indiana University":                            "https://www.iu.edu",
    "University of Iowa":                            "https://uiowa.edu",
    "Iowa State University":                         "https://www.iastate.edu",
    "University of Minnesota":                       "https://twin-cities.umn.edu",
    "University of Wisconsin-Madison":               "https://www.wisc.edu",
    "University of Wisconsin Madison":               "https://www.wisc.edu",
    "University of Illinois Urbana-Champaign":       "https://illinois.edu",
    "University of Illinois":                        "https://illinois.edu",
    "Michigan State University":                     "https://msu.edu",
    "University of Maryland":                        "https://www.umd.edu",
    "University of Missouri":                        "https://www.missouri.edu",
    "Louisiana State University":                    "https://www.lsu.edu",
    "LSU":                                           "https://www.lsu.edu",
    "Texas Tech University":                         "https://www.ttu.edu",
    "Virginia Tech":                                 "https://www.vt.edu",
    "University of Tennessee":                       "https://www.utk.edu",
    "University of Kentucky":                        "https://www.uky.edu",
    "University of Alabama":                         "https://www.ua.edu",
    "Auburn University":                             "https://www.auburn.edu",
    "University of Florida":                         "https://www.ufl.edu",
    "University of Georgia":                         "https://www.uga.edu",
    "University of South Carolina":                  "https://sc.edu",
    "University of Arkansas":                        "https://www.uark.edu",
    "University of Colorado Boulder":                "https://www.colorado.edu",
    "University of Oregon":                          "https://www.uoregon.edu",
    "University of Washington":                      "https://www.washington.edu",
    "University of Utah":                            "https://www.utah.edu",
    "University of Arizona":                         "https://www.arizona.edu",
    "Arizona State University":                      "https://www.asu.edu",
    "University of Miami":                           "https://www.miami.edu",
    "Rutgers University":                            "https://www.rutgers.edu",
    "University of Delaware":                        "https://www.udel.edu",
    "College of the Holy Cross":                     "https://www.holycross.edu",
    "Claremont McKenna College":                     "https://www.cmc.edu",
    "Harvey Mudd College":                           "https://www.hmc.edu",
    "Hobart and William Smith Colleges":             "https://www.hws.edu",
    "Connecticut College":                           "https://www.conncoll.edu",
    "Trinity College":                               "https://www.trincoll.edu",
    "St. Olaf College":                              "https://stolaf.edu",
    "Gustavus Adolphus College":                     "https://gustavus.edu",
    "Rose-Hulman Institute of Technology":           "https://www.rose-hulman.edu",
    "College of Wooster":                            "https://www.wooster.edu",
    "Ohio Wesleyan University":                      "https://www.owu.edu",
    "Baldwin Wallace University":                    "https://www.bw.edu",
    "John Carroll University":                       "https://www.jcu.edu",
    "Ohio Northern University":                      "https://www.onu.edu",
    "University of Findlay":                         "https://www.findlay.edu",
    "Mount Vernon Nazarene University":              "https://www.mvnu.edu",
    "Franklin & Marshall College":                   "https://www.fandm.edu",
    "Lehigh University":                             "https://www1.lehigh.edu",
    "Villanova University":                          "https://www1.villanova.edu",
    "Temple University":                             "https://www.temple.edu",
    "Drexel University":                             "https://drexel.edu",
    "Saint Joseph's University":                     "https://www.sju.edu",
    "George Washington University":                  "https://www.gwu.edu",
    "Howard University":                             "https://home.howard.edu",
    "University of Maryland Baltimore County":       "https://umbc.edu",
    "UMBC":                                          "https://umbc.edu",
    "Johns Hopkins University":                      "https://www.jhu.edu",
    "Loyola University Maryland":                    "https://www.loyola.edu",
    "Virginia Military Institute":                   "https://www.vmi.edu",
    "Randolph-Macon College":                        "https://www.rmc.edu",
    "James Madison University":                      "https://www.jmu.edu",
    "George Mason University":                       "https://www2.gmu.edu",
    "Christopher Newport University":                "https://www.cnu.edu",
    "Norfolk State University":                      "https://www.nsu.edu",
    "Duke University":                               "https://www.duke.edu",
    "Davidson College":                              "https://www.davidson.edu",
    "Elon University":                               "https://www.elon.edu",
    "Lenoir-Rhyne University":                       "https://www.lr.edu",
    "Wingate University":                            "https://www.wingate.edu",
    "Queens University of Charlotte":                "https://www.queens.edu",
    "Presbyterian College":                          "https://www.presby.edu",
    "Emory University":                              "https://www.emory.edu",
    "Agnes Scott College":                           "https://www.agnesscott.edu",
    "Covenant College":                              "https://covenant.edu",
    "Kennesaw State University":                     "https://www.kennesaw.edu",
    "Georgia College":                               "https://www.gcsu.edu",
    "Georgia State University":                      "https://www.gsu.edu",
    "Georgia Southern University":                   "https://www.georgiasouthern.edu",
    "Sewanee":                                       "https://new.sewanee.edu",
    "Sewanee: The University of the South":          "https://new.sewanee.edu",
    "University of the South":                       "https://new.sewanee.edu",
    "Belhaven University":                           "https://www.belhaven.edu",
    "Mississippi College":                           "https://www.mc.edu",
    "Delta State University":                        "https://www.deltastate.edu",
    "Ouachita Baptist University":                   "https://www.obu.edu",
    "University of Dubuque":                         "https://www.dbq.edu",
    "Illinois Wesleyan University":                  "https://www.iwu.edu",
    "North Central College":                         "https://www.northcentralcollege.edu",
    "Concordia University Chicago":                  "https://www.cuchicago.edu",
    "Benedictine University":                        "https://www.ben.edu",
    "University of St. Francis":                     "https://www.stfrancis.edu",
    "Upper Iowa University":                         "https://uiu.edu",
    "Cornell College":                               "https://www.cornellcollege.edu",
    "Cornell University":                            "https://www.cornell.edu",
    "Missouri University of Science and Technology": "https://www.mst.edu",
    "University of Central Missouri":                "https://www.ucmo.edu",
    "Southwest Baptist University":                  "https://www.sbuniv.edu",
    "Missouri Baptist University":                   "https://www.mobap.edu",
    "Maryville University":                          "https://www.maryville.edu",
    "William Jewell College":                        "https://william.jewell.edu",
    "Westminster College (MO)":                      "https://www.westminster-mo.edu",
    "Southeast Missouri State University":           "https://www.semo.edu",
    "Northwest Missouri State University":           "https://www.nwmissouri.edu",
    "Missouri Western State University":             "https://www.missouriwestern.edu",
    "Missouri Southern State University":            "https://www.mssu.edu",
    "Trinity University":                            "https://www.trinity.edu",
    "University of Texas at Austin":                 "https://www.utexas.edu",
    "UT Austin":                                     "https://www.utexas.edu",
    "Texas Christian University":                    "https://www.tcu.edu",
    "Southern Methodist University":                 "https://www.smu.edu",
    "University of the Incarnate Word":              "https://www.uiw.edu",
    "Texas Lutheran University":                     "https://www.tlu.edu",
    "Hardin-Simmons University":                     "https://www.hsutx.edu",
    "LeTourneau University":                         "https://www.letu.edu",
    "University of Denver":                          "https://www.du.edu",
    "Colorado School of Mines":                      "https://www.mines.edu",
    "Western Colorado University":                   "https://western.edu",
    "University of Northern Colorado":               "https://www.unco.edu",
    "Fort Lewis College":                            "https://www.fortlewis.edu",
    "Colorado State University":                     "https://www.colostate.edu",
    "Utah State University":                         "https://www.usu.edu",
    "Brigham Young University":                      "https://www.byu.edu",
    "BYU":                                           "https://www.byu.edu",
    "Weber State University":                        "https://www.weber.edu",
    "Southern Utah University":                      "https://www.suu.edu",
    "Utah Valley University":                        "https://www.uvu.edu",
    "Westminster College (UT)":                      "https://www.westminstercollege.edu",
    "University of Idaho":                           "https://www.uidaho.edu",
    "Idaho State University":                        "https://www.isu.edu",
    "Boise State University":                        "https://www.boisestate.edu",
    "College of Idaho":                              "https://www.collegeofidaho.edu",
    "Montana State University":                      "https://www.montana.edu",
    "University of Montana":                         "https://www.umt.edu",
    "University of Nevada Las Vegas":                "https://www.unlv.edu",
    "UNLV":                                          "https://www.unlv.edu",
    "University of Nevada Reno":                     "https://www.unr.edu",
    "University of Nevada":                          "https://www.unr.edu",
    "Oregon State University":                       "https://oregonstate.edu",
    "Portland State University":                     "https://www.pdx.edu",
    "Lewis & Clark College":                         "https://www.lclark.edu",
    "George Fox University":                         "https://www.georgefox.edu",
    "Gonzaga University":                            "https://www.gonzaga.edu",
    "Seattle University":                            "https://www.seattleu.edu",
    "Eastern Washington University":                 "https://www.ewu.edu",
    "Pacific Lutheran University":                   "https://www.plu.edu",
    "University of Puget Sound":                     "https://www.pugetsound.edu",
    "Puget Sound":                                   "https://www.pugetsound.edu",
    "Stanford University":                           "https://www.stanford.edu",
    "University of San Francisco":                   "https://www.usfca.edu",
    "University of San Diego":                       "https://www.sandiego.edu",
    "Santa Clara University":                        "https://www.scu.edu",
    "Loyola Marymount University":                   "https://www.lmu.edu",
    "University of the Pacific":                     "https://www.pacific.edu",
    "California Lutheran University":                "https://www.callutheran.edu",
    "Point Loma Nazarene University":                "https://www.pointloma.edu",
    "Azusa Pacific University":                      "https://www.apu.edu",
    "Cal Poly San Luis Obispo":                      "https://www.calpoly.edu",
    "California Polytechnic State University":       "https://www.calpoly.edu",
    "Cal Poly SLO":                                  "https://www.calpoly.edu",
    "Cal State Long Beach":                          "https://www.csulb.edu",
    "Cal State Fullerton":                           "https://www.fullerton.edu",
    "Cal State Northridge":                          "https://www.csun.edu",
    "San Diego State University":                    "https://www.sdsu.edu",
    "San Jose State University":                     "https://www.sjsu.edu",
    "Chico State":                                   "https://www.csuchico.edu",
    "Fresno State":                                  "https://www.fresnostate.edu",
    "University of Hawaii":                          "https://manoa.hawaii.edu",
    "University of Hawaii at Manoa":                 "https://manoa.hawaii.edu",
    "Fordham University":                            "https://www.fordham.edu",
    "Vassar College":                                "https://www.vassar.edu",
    "Clarkson University":                           "https://www.clarkson.edu",
    "St. Lawrence University":                       "https://www.stlawu.edu",
    "University of Rochester":                       "https://www.rochester.edu",
    "SUNY Geneseo":                                  "https://www.geneseo.edu",
    "SUNY Oswego":                                   "https://www.oswego.edu",
    "SUNY Cortland":                                 "https://www.cortland.edu",
    "SUNY New Paltz":                                "https://www.newpaltz.edu",
    "SUNY Brockport":                                "https://www.brockport.edu",
    "SUNY Fredonia":                                 "https://www.fredonia.edu",
    "Stony Brook University":                        "https://www.stonybrook.edu",
    "Seton Hall University":                         "https://www.shu.edu",
    "Fairleigh Dickinson University":                "https://www.fdu.edu",
    "Montclair State University":                    "https://www.montclair.edu",
    "College of New Jersey":                         "https://www.tcnj.edu",
    "TCNJ":                                          "https://www.tcnj.edu",
    "Stevens Institute of Technology":               "https://www.stevens.edu",
    "Sacred Heart University":                       "https://www.sacredheart.edu",
    "Yale University":                               "https://www.yale.edu",
    "Brown University":                              "https://www.brown.edu",
    "Dartmouth College":                             "https://home.dartmouth.edu",
    "Harvard University":                            "https://www.harvard.edu",
    "Princeton University":                          "https://www.princeton.edu",
    "Columbia University":                           "https://www.columbia.edu",
    "University of Pennsylvania":                    "https://www.upenn.edu",
    "UPenn":                                         "https://www.upenn.edu",
    "Wesleyan University":                           "https://www.wesleyan.edu",
    "Tufts University":                              "https://www.tufts.edu",
    "Brandeis University":                           "https://www.brandeis.edu",
    "Clark University":                              "https://www.clarku.edu",
    "Springfield College":                           "https://springfieldcollege.edu",
    "Western New England University":                "https://www1.wne.edu",
    "Wheaton College (MA)":                          "https://wheatoncollege.edu",
    "Wheaton College (IL)":                          "https://www.wheaton.edu",
    "Simpson College":                               "https://simpson.edu",
    "Hope College":                                  "https://hope.edu",
    "Calvin University":                             "https://calvin.edu",
    "Kalamazoo College":                             "https://www.kzoo.edu",
    "DePauw University":                             "https://www.depauw.edu",
    "Hanover College":                               "https://www.hanover.edu",
    "Earlham College":                               "https://www.earlham.edu",
    "Ohio Wesleyan University":                      "https://www.owu.edu",
    "Otterbein University":                          "https://www.otterbein.edu",
    "Heidelberg University":                         "https://www.heidelberg.edu",
    "Muskingum University":                          "https://www.muskingum.edu",
    "University of Mount Union":                     "https://www.mountunion.edu",
    "Notre Dame College":                            "https://www.notredamecollege.edu",
    "Franciscan University of Steubenville":         "https://www.franciscan.edu",
    "Tiffin University":                             "https://www.tiffin.edu",
    "Ashland University":                            "https://www.ashland.edu",
    "Cedarville University":                         "https://www.cedarville.edu",
    "Wilmington College":                            "https://www.wilmington.edu",
    "Thiel College":                                 "https://www.thiel.edu",
    "Grove City College":                            "https://www.gcc.edu",
    "Juniata College":                               "https://www.juniata.edu",
    "Elizabethtown College":                         "https://www.etown.edu",
    "Messiah University":                            "https://www.messiah.edu",
    "Susquehanna University":                        "https://www.susqu.edu",
    "Muhlenberg College":                            "https://www.muhlenberg.edu",
    "Ursinus College":                               "https://www.ursinus.edu",
    "Swarthmore College":                            "https://www.swarthmore.edu",
    "Haverford College":                             "https://www.haverford.edu",
    "Bryn Mawr College":                             "https://www.brynmawr.edu",
    "Lafayette College":                             "https://www.lafayette.edu",
    "Moravian University":                           "https://www.moravian.edu",
    "Misericordia University":                       "https://www.misericordia.edu",
    "Lycoming College":                              "https://www.lycoming.edu",
    "Lock Haven University":                         "https://www.lockhaven.edu",
    "Shippensburg University":                       "https://www.ship.edu",
    "Millersville University":                       "https://www.millersville.edu",
    "Kutztown University":                           "https://www.kutztown.edu",
    "East Stroudsburg University":                   "https://www.esu.edu",
    "Slippery Rock University":                      "https://www.sru.edu",
    "Gannon University":                             "https://www.gannon.edu",
    "Mercyhurst University":                         "https://www.mercyhurst.edu",
    "Seton Hill University":                         "https://www.setonhill.edu",
    "Indiana University of Pennsylvania":            "https://www.iup.edu",
    "Duquesne University":                           "https://www.duq.edu",
    "Robert Morris University":                      "https://www.rmu.edu",
    "Georgetown University":                         "https://www.georgetown.edu",
    "Catholic University of America":                "https://www.catholic.edu",
    "American University":                           "https://www.american.edu",
    "McDaniel College":                              "https://www.mcdaniel.edu",
    "Washington College":                            "https://www.washcoll.edu",
    "Goucher College":                               "https://www.goucher.edu",
    "St. Mary's College of Maryland":                "https://www.smcm.edu",
    "Frostburg State University":                    "https://www.frostburg.edu",
    "Virginia Wesleyan University":                  "https://www.vwu.edu",
    "Randolph College":                              "https://www.randolphcollege.edu",
    "Eastern Mennonite University":                  "https://www.emu.edu",
    "Radford University":                            "https://www.radford.edu",
    "Liberty University":                            "https://www.liberty.edu",
    "Hampton University":                            "https://home.hamptonu.edu",
    "Wake Forest University":                        "https://www.wfu.edu",
    "High Point University":                         "https://www.highpoint.edu",
    "Warren Wilson College":                         "https://www.warren-wilson.edu",
    "Pfeiffer University":                           "https://www.pfeiffer.edu",
    "Lenoir-Rhyne University":                       "https://www.lr.edu",
    "Mars Hill University":                          "https://www.mhu.edu",
    "Belmont Abbey College":                         "https://belmontabbeycollege.edu",
    "Coker University":                              "https://www.coker.edu",
    "Berry College":                                 "https://www.berry.edu",
    "Piedmont University":                           "https://www.piedmont.edu",
    "LaGrange College":                              "https://www.lagrange.edu",
    "Shorter University":                            "https://www.shorter.edu",
    "Reinhardt University":                          "https://www.reinhardt.edu",
    "University of West Georgia":                    "https://www.westga.edu",
    "Valdosta State University":                     "https://www.valdosta.edu",
    "Columbus State University":                     "https://www.columbusstate.edu",
    "Birmingham-Southern College":                   "https://www.bsc.edu",
    "Millsaps College":                              "https://www.millsaps.edu",
    "Samford University":                            "https://www.samford.edu",
    "Hendrix College":                               "https://www.hendrix.edu",
    "Ouachita Baptist University":                   "https://www.obu.edu",
    "Lyon College":                                  "https://www.lyon.edu",
    "Loras College":                                 "https://www.loras.edu",
    "Wartburg College":                              "https://www.wartburg.edu",
    "Buena Vista University":                        "https://www.bvu.edu",
    "Coe College":                                   "https://www.coe.edu",
    "University of Dubuque":                         "https://www.dbq.edu",
    "Clarke University":                             "https://www.clarke.edu",
    "Albion College":                                "https://www.albion.edu",
    "Hope College":                                  "https://hope.edu",
    "Olivet College":                                "https://www.olivetcollege.edu",
    "Defiance College":                              "https://www.defiance.edu",
    "Bluffton University":                           "https://www.bluffton.edu",
    "Manchester University":                         "https://www.manchester.edu",
    "Anderson University":                           "https://www.anderson.edu",
    "Wabash College":                                "https://www.wabash.edu",
    "Illinois College":                              "https://www.ic.edu",
    "Principia College":                             "https://www.principia.edu",
    "Benedictine College":                           "https://www.benedictine.edu",
    "Central College":                               "https://www.central.edu",
    "Drury University":                              "https://www.drury.edu",
    "Truman State University":                       "https://www.truman.edu",
    "Rockhurst University":                          "https://www.rockhurst.edu",
    "University of Arkansas Fort Smith":             "https://www.uafs.edu",
    "Southwestern University":                       "https://www.southwestern.edu",
    "Austin College":                                "https://www.austincollege.edu",
    "McMurry University":                            "https://www.mcm.edu",
    "Abilene Christian University":                  "https://www.acu.edu",
    "Howard Payne University":                       "https://www.hputx.edu",
    "Regis University":                              "https://www.regis.edu",
    "Adams State University":                        "https://www.adams.edu",
    "Pacific Lutheran University":                   "https://www.plu.edu",
    "Whitworth University":                          "https://www.whitworth.edu",
    "Linfield University":                           "https://www.linfield.edu",
    "Willamette University":                         "https://www.willamette.edu",
    "Pacific University":                            "https://www.pacificu.edu",
    "Reed College":                                  "https://www.reed.edu",
    "Chapman University":                            "https://www.chapman.edu",
    "Whittier College":                              "https://www.whittier.edu",
    "Biola University":                              "https://www.biola.edu",
    "Vanguard University":                           "https://www.vanguard.edu",
    "Chaminade University":                          "https://www.chaminade.edu",
    "Hawaii Pacific University":                     "https://www.hpu.edu",
    "Manhattan College":                             "https://manhattan.edu",
    "Iona University":                               "https://www.iona.edu",
    "St. John's University":                         "https://www.stjohns.edu",
    "Wagner College":                                "https://wagner.edu",
    "Hartwick College":                              "https://www.hartwick.edu",
    "Utica University":                              "https://www.utica.edu",
    "Ithaca College":                                "https://www.ithaca.edu",
    "Alfred University":                             "https://www.alfred.edu",
    "Drew University":                               "https://www.drew.edu",
    "Rider University":                              "https://www.rider.edu",
    "Stockton University":                           "https://www.stockton.edu",
    "William Paterson University":                   "https://www.wpunj.edu",
    "Ramapo College":                                "https://www.ramapo.edu",
    "Rowan University":                              "https://www.rowan.edu",
    "Kean University":                               "https://www.kean.edu",
    "Fairfield University":                          "https://www.fairfield.edu",
    "Quinnipiac University":                         "https://www.qu.edu",
    "Marist College":                                "https://www.marist.edu",
    "Bard College":                                  "https://www.bard.edu",
    "Sarah Lawrence College":                        "https://www.sarahlawrence.edu",
    "Pace University":                               "https://www.pace.edu",
    "Macalester College":                            "https://www.macalester.edu",
    "St. Thomas University":                         "https://www.stthomas.edu",
    "Luther College":                                "https://www.luther.edu",
    "Carleton College":                              "https://www.carleton.edu",
    "Colorado College":                              "https://www.coloradocollege.edu",
    "Centre College":                                "https://www.centre.edu",
    "Rhodes College":                                "https://www.rhodes.edu",
    "Bates College":                                 "https://www.bates.edu",
    "Bowdoin College":                               "https://www.bowdoin.edu",
    "Colby College":                                 "https://www.colby.edu",
    "Middlebury College":                            "https://www.middlebury.edu",
    "Williams College":                              "https://www.williams.edu",
    "Amherst College":                               "https://www.amherst.edu",
    "Hamilton College":                              "https://www.hamilton.edu",
    "Colgate University":                            "https://www.colgate.edu",
    "Union College":                                 "https://www.union.edu",
    "Skidmore College":                              "https://www.skidmore.edu",
    "Grinnell College":                              "https://www.grinnell.edu",
    "Kenyon College":                                "https://www.kenyon.edu",
    "Hiram College":                                 "https://www.hiram.edu",
    "Oberlin College":                               "https://www.oberlin.edu",
    "Denison University":                            "https://denison.edu",
    "Allegheny College":                             "https://www.allegheny.edu",
    "Westminster College (PA)":                      "https://www.westminster.edu",
    "Waynesburg University":                         "https://www.waynesburg.edu",
    "Chatham University":                            "https://www.chatham.edu",
    "Bucknell University":                           "https://www.bucknell.edu",
    "Gettysburg College":                            "https://www.gettysburg.edu",
    "Dickinson College":                             "https://www.dickinson.edu",
    "Augustana College":                             "https://www.augustana.edu",
    "Gustavus Adolphus College":                     "https://gustavus.edu",
    "Beloit College":                                "https://www.beloit.edu",
    "Lawrence University":                           "https://www.lawrence.edu",
    "Ripon College":                                 "https://www.ripon.edu",
    "Monmouth College":                              "https://www.monmouthcollege.edu",
}

# ── junk URL/filename patterns ─────────────────────────────────────────────────
JUNK_PATTERNS = re.compile(
    r'logo|seal|crest|icon|sprite|badge|wordmark|mascot|patch|insignia|'
    r'avatar|thumb(?:nail)?|placeholder|loading|\.gif$|social|'
    r'tracking|pixel|ad[-_]|promo[-_]tile|banner[-_]ad|'
    r'donor|calendar|map|chart|graphic|lockup|tile[-_]|'
    r'bullet|arrow|check|star|rating|spinner|blank|empty|'
    r'facebook|twitter|instagram|linkedin|youtube|tiktok',
    re.I
)

# ── role-specific keyword scoring ──────────────────────────────────────────────
HERO_POS = ['campus', 'aerial', 'quad', 'grounds', 'courtyard', 'panoram',
            'entrance', 'lawn', 'building', 'hall', 'chapel', 'library',
            'tower', 'center', 'science', 'view', 'arch', 'fountain',
            'gateway', 'green', 'exterior', 'academic']
HERO_NEG = ['portrait', 'headshot', 'rally', 'protest', 'game', 'stadium',
            'pool', 'swim', 'athlete', 'jersey', 'locker']

STUDENT_POS = ['student', 'students', 'class', 'lecture', 'graduation',
               'commencement', 'activity', 'club', 'dining', 'reading',
               'study', 'campus-life', 'residential', 'community', 'people',
               'life', 'engage', 'experience', 'collaborate']
STUDENT_NEG = ['aerial', 'exterior', 'tower', 'pool', 'swim', 'locker',
               'stadium', 'stadium', 'athlete']

SWIM_STRONG  = ['swim', 'swimming', 'diving', 'swim-dive', 'natator',
                'aquat', 'aquatic', 'pool', 'championship-pool']
SWIM_OK      = ['athletic', 'sport', 'recreation', 'fitness']
SWIM_REJECT  = ['football', 'basketball', 'baseball', 'softball', 'soccer',
                'lacrosse', 'tennis', 'volleyball', 'hockey', 'golf',
                'mascot', 'jersey', 'locker']

# Cross-school dedup: if ≥ this many schools have same URL, it's generic.
CROSS_SCHOOL_MAX = 4


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — WEBSITE RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def _name_to_slug(name: str) -> str:
    n = name.lower().strip()
    for prefix in ['the ', 'university of ', 'college of ', 'institute of ',
                   'school of ']:
        if n.startswith(prefix):
            n = n[len(prefix):]
    for suffix in [' university', ' college', ' institute', ' school',
                   ' polytechnic']:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
            break
    return re.sub(r'[^a-z0-9]', '', n)


def _verify_url(url: str) -> bool:
    try:
        r = requests.head(url, timeout=TIMEOUT, allow_redirects=True,
                          headers={'User-Agent': UA})
        return r.status_code < 400
    except Exception:
        return False


def resolve_website(school: str, cache: dict) -> str | None:
    if school in cache:
        return cache[school]

    if school in WEBSITE_OVERRIDES:
        url = WEBSITE_OVERRIDES[school]
        cache[school] = url
        return url

    slug = _name_to_slug(school)
    if not slug:
        cache[school] = None
        return None

    candidates = [
        f'https://www.{slug}.edu',
        f'https://{slug}.edu',
    ]
    # For "University of X" / "X State University" patterns
    name_lower = school.lower()
    if 'state' in name_lower:
        state_slug = re.sub(r'[^a-z0-9]', '', name_lower
                            .replace('state university', '').replace('state college', '').strip())
        if state_slug:
            candidates += [f'https://www.{state_slug}state.edu',
                           f'https://{state_slug}state.edu']

    for url in candidates:
        if _verify_url(url):
            cache[school] = url
            return url

    cache[school] = None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — PAGE FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def fetch_html(url: str, use_cache=True) -> str | None:
    key  = _cache_key(url)
    path = PAGE_CACHE / f'{key}.html'
    if use_cache and path.exists():
        return path.read_text(errors='replace')
    try:
        r = requests.get(url, timeout=TIMEOUT,
                         headers={'User-Agent': UA}, allow_redirects=True)
        if r.status_code == 200 and 'text/html' in r.headers.get('content-type', ''):
            path.write_text(r.text, errors='replace')
            return r.text
    except Exception:
        pass
    return None


def _try_paths(base: str, paths: list[str]) -> str | None:
    """Return first path under base that returns 200, or None."""
    for p in paths:
        url = base.rstrip('/') + p
        try:
            r = requests.head(url, timeout=TIMEOUT, allow_redirects=True,
                              headers={'User-Agent': UA})
            if r.status_code < 400:
                return url
        except Exception:
            continue
    return None


def get_target_pages(base: str) -> list[tuple[str, str]]:
    """Return (page_type, url) pairs to fetch, up to MAX_PAGES."""
    pages: list[tuple[str, str]] = []

    pages.append(('homepage', base.rstrip('/') + '/'))

    adm = _try_paths(base, ['/admissions', '/admission', '/apply',
                             '/undergraduate-admission',
                             '/undergraduate-admissions'])
    if adm:
        pages.append(('admissions', adm))

    life = _try_paths(base, ['/student-life', '/campus-life',
                              '/campus', '/student-experience',
                              '/life-at-campus'])
    if life:
        pages.append(('student_life', life))

    ath = _try_paths(base, ['/athletics', '/sports', '/athletic'])
    if ath:
        pages.append(('athletics', ath))

    for swim_path in ['/sports/mens-swimming-and-diving',
                      '/sports/womens-swimming-and-diving',
                      '/sports/swimming-and-diving',
                      '/sports/swimming',
                      '/sports/swim-and-dive',
                      '/sports/mens-swimming',
                      '/sports/womens-swimming']:
        url = _try_paths(base, [swim_path])
        if url:
            pages.append(('swim', url))
            break

    if ath and len(pages) < MAX_PAGES:
        html = fetch_html(ath)
        if html:
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href'].lower()
                if any(k in href for k in ['swim', 'aquat', 'natator', 'pool']):
                    full = urljoin(ath, a['href'])
                    if full not in [u for _, u in pages]:
                        pages.append(('swim', full))
                        break

    return pages[:MAX_PAGES]


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — IMAGE EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _best_srcset(srcset: str) -> str:
    """Pick highest-width descriptor from a srcset attribute."""
    best_url, best_w = '', 0
    for part in srcset.split(','):
        part = part.strip()
        pieces = part.split()
        if not pieces:
            continue
        url = pieces[0]
        w = 0
        if len(pieces) > 1 and pieces[1].endswith('w'):
            try:
                w = int(pieces[1][:-1])
            except ValueError:
                pass
        if w > best_w:
            best_w, best_url = w, url
    return best_url or srcset.split(',')[0].strip().split()[0]


def extract_candidates(html: str, page_url: str, page_type: str) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    candidates = []
    seen_urls: set[str] = set()

    def _add(raw_url: str, alt: str, context: str, from_srcset=False,
             from_og=False):
        url = urljoin(page_url, raw_url)
        url = url.split('?')[0]          # strip query params for normalization
        if not url.startswith('http'):
            return
        ext = url.rsplit('.', 1)[-1].lower() if '.' in url.rsplit('/', 1)[-1] else ''
        if ext in ('svg', 'gif', 'ico', 'webp'):
            if ext == 'webp':
                pass       # allow webp
            else:
                return
        if ext and ext not in ('jpg', 'jpeg', 'png', 'webp'):
            return
        fname = url.rsplit('/', 1)[-1].lower()
        combined = f'{fname} {alt.lower()} {url.lower()}'
        if JUNK_PATTERNS.search(combined):
            return
        if url in seen_urls:
            return
        seen_urls.add(url)
        candidates.append({
            'url':       url,
            'alt':       alt,
            'page_type': page_type,
            'page_url':  page_url,
            'fname':     fname,
            'context':   context[:200],
            'from_og':   from_og,
        })

    # og:image (fallback only — mark it)
    og = soup.find('meta', property='og:image')
    if og and og.get('content'):
        _add(og['content'], '', 'og:image', from_og=True)

    # <img> tags
    for img in soup.find_all('img'):
        raw = img.get('data-src') or img.get('data-lazy-src') or img.get('src', '')
        srcset = img.get('srcset') or img.get('data-srcset', '')
        alt = img.get('alt', '')
        nearby = ''
        for parent in img.parents:
            if parent.name in ('figure', 'div', 'section', 'article'):
                nearby = parent.get_text(' ', strip=True)[:200]
                break
        if srcset:
            _add(_best_srcset(srcset), alt, nearby, from_srcset=True)
        if raw:
            _add(raw, alt, nearby)

    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — SCORING
# ─────────────────────────────────────────────────────────────────────────────

def _kw_score(text: str, positives: list, negatives: list) -> int:
    t = text.lower()
    s = sum(6 for kw in positives if kw in t)
    s -= sum(5 for kw in negatives if kw in t)
    return s


def score_hero(c: dict) -> int:
    if c.get('from_og'):
        return -10
    combined = f"{c['fname']} {c['alt']} {c['context']}"
    s = _kw_score(combined, HERO_POS, HERO_NEG)
    if c['page_type'] in ('homepage', 'admissions'):
        s += 8
    elif c['page_type'] == 'student_life':
        s += 4
    elif c['page_type'] in ('athletics', 'swim'):
        s -= 10
    return s


def score_student(c: dict) -> int:
    if c.get('from_og'):
        return -10
    combined = f"{c['fname']} {c['alt']} {c['context']}"
    s = _kw_score(combined, STUDENT_POS, STUDENT_NEG)
    if c['page_type'] == 'student_life':
        s += 10
    elif c['page_type'] == 'admissions':
        s += 5
    elif c['page_type'] == 'homepage':
        s += 2
    elif c['page_type'] in ('athletics', 'swim'):
        s -= 10
    return s


def score_swim(c: dict) -> int:
    combined = f"{c['fname']} {c['alt']} {c['context']} {c['page_url']}"
    t = combined.lower()
    # Hard reject non-swim sports
    if any(kw in t for kw in SWIM_REJECT):
        return -50
    s = 0
    strong_hit = any(kw in t for kw in SWIM_STRONG)
    if strong_hit:
        s += 20
    if any(kw in t for kw in SWIM_OK):
        s += 5
    if c['page_type'] == 'swim':
        s += 15
    elif c['page_type'] == 'athletics':
        s += 3
    elif c['page_type'] not in ('swim', 'athletics'):
        s -= 15
    return s


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — SELECTION PER SCHOOL
# ─────────────────────────────────────────────────────────────────────────────

def select_images(school: str, candidates: list[dict],
                  cross_used: set) -> dict:
    def best(score_fn, exclude_urls: set, min_score: int) -> tuple[str | None, bool]:
        scored = sorted(
            [(score_fn(c), c) for c in candidates
             if c['url'] not in exclude_urls and c['url'] not in cross_used],
            key=lambda x: x[0], reverse=True
        )
        for sc, c in scored:
            if sc >= min_score:
                return c['url'], False
        # fallback: any non-junk OG image
        for sc, c in scored:
            if sc >= -20:
                return c['url'], True
        return None, True

    chosen: dict[str, str | None] = {}
    fallback: dict[str, bool]     = {}
    used: set[str]                = set()

    hero_url, hero_fb = best(score_hero, used, min_score=0)
    chosen['hero']    = hero_url
    fallback['hero']  = hero_fb
    if hero_url:
        used.add(hero_url)

    sl_url, sl_fb     = best(score_student, used, min_score=0)
    chosen['student_life']  = sl_url
    fallback['student_life'] = sl_fb
    if sl_url:
        used.add(sl_url)

    sw_url, sw_fb     = best(score_swim, used, min_score=-5)
    chosen['swim']    = sw_url
    fallback['swim']  = sw_fb

    return {
        'hero':          chosen['hero'],
        'student_life':  chosen['student_life'],
        'swim':          chosen['swim'],
        'is_fallback':   fallback,
        'source_pages': {
            role: next((c['page_type'] for c in candidates
                        if c['url'] == chosen[role]), None)
            for role in ('hero', 'student_life', 'swim')
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 — CROSS-SCHOOL DEDUP
# ─────────────────────────────────────────────────────────────────────────────

def cross_school_dedupe(manifest: dict) -> dict:
    for role in ('hero', 'student_life', 'swim'):
        url_count: Counter = Counter(
            v[role] for v in manifest.values() if v.get(role)
        )
        for url, cnt in url_count.items():
            if cnt > CROSS_SCHOOL_MAX:
                cleared = 0
                for imgs in manifest.values():
                    if imgs.get(role) == url:
                        imgs[role] = None
                        imgs['is_fallback'][role] = True
                        cleared += 1
                print(f'  [XDEDUPE] {role}: cleared {cleared} schools '
                      f'sharing "{url[:70]}"')
    return manifest


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────────────────

def audit(manifest: dict):
    n = len(manifest)
    print(f'\n=== MANIFEST AUDIT ({n} schools) ===')
    for role in ('hero', 'student_life', 'swim'):
        have    = sum(1 for v in manifest.values() if v.get(role))
        fb      = sum(1 for v in manifest.values()
                      if v.get('is_fallback', {}).get(role))
        print(f'  {role:12s}: {have:3d}/{n} real  '
              f'({n-have:3d} null  {fb:3d} fallback)')


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def process_school(school: str, base_url: str) -> tuple[dict, dict]:
    """Fetch pages, extract, score, return (result, debug)."""
    pages    = get_target_pages(base_url)
    all_cand = []
    debug_pages = []

    for page_type, page_url in pages:
        html = fetch_html(page_url)
        if not html:
            debug_pages.append({'type': page_type, 'url': page_url,
                                 'fetched': False})
            continue
        cands = extract_candidates(html, page_url, page_type)
        debug_pages.append({'type': page_type, 'url': page_url,
                             'fetched': True, 'candidates': len(cands)})
        all_cand.extend(cands)

    result = select_images(school, all_cand, set())

    debug = {
        'school':     school,
        'base_url':   base_url,
        'pages':      debug_pages,
        'total_candidates': len(all_cand),
        'chosen': {
            'hero': {
                'url':     result['hero'],
                'page':    result['source_pages']['hero'],
                'fallback': result['is_fallback']['hero'],
            },
            'student_life': {
                'url':     result['student_life'],
                'page':    result['source_pages']['student_life'],
                'fallback': result['is_fallback']['student_life'],
            },
            'swim': {
                'url':     result['swim'],
                'page':    result['source_pages']['swim'],
                'fallback': result['is_fallback']['swim'],
            },
        }
    }
    return result, debug


def needs_fetch(school: str, existing: dict, force: bool) -> bool:
    if force or school not in existing:
        return True
    e = existing[school]
    return (not e.get('hero') or
            not e.get('student_life') or
            not e.get('swim') or
            e.get('is_fallback', {}).get('hero') or
            e.get('is_fallback', {}).get('swim'))


def build(school_filter=None, sample=None, force=False):
    sys.path.insert(0, str(ROOT_DIR))
    import main as m
    all_schools = sorted(m.SCHOOL_META.keys())

    if school_filter:
        all_schools = [s for s in all_schools
                       if school_filter.lower() in s.lower()]
    if sample:
        all_schools = all_schools[:sample]

    print(f'[images] {len(all_schools)} school(s) targeted')

    manifest = {}
    if MANIFEST.exists() and not force:
        manifest = json.loads(MANIFEST.read_text())

    site_cache: dict = {}
    if SITES_FILE.exists():
        site_cache = json.loads(SITES_FILE.read_text())

    debug_all = {}
    if DEBUG_OUT.exists():
        debug_all = json.loads(DEBUG_OUT.read_text())

    to_fetch = [s for s in all_schools if needs_fetch(s, manifest, force)]
    print(f'[images] {len(to_fetch)} to fetch  |  '
          f'{len(all_schools)-len(to_fetch)} already complete')

    for i, school in enumerate(to_fetch, 1):
        base = resolve_website(school, site_cache)
        if not base:
            print(f'  [{i:3}/{len(to_fetch)}] {school} — no website found, skipping')
            manifest[school] = {
                'hero': None, 'student_life': None, 'swim': None,
                'is_fallback': {'hero': True, 'student_life': True,
                                'swim': True},
                'source_pages': {'hero': None, 'student_life': None,
                                 'swim': None},
            }
            continue

        print(f'  [{i:3}/{len(to_fetch)}] {school}  →  {base}')
        result, debug = process_school(school, base)
        manifest[school]  = result
        debug_all[school] = debug

        h = (result.get('hero') or 'null')[:70]
        w = (result.get('swim') or 'null')[:70]
        print(f'         hero: {h}')
        print(f'         swim: {w}')

        if i % 10 == 0 or i == len(to_fetch):
            MANIFEST.write_text(json.dumps(manifest, indent=2))
            SITES_FILE.write_text(json.dumps(site_cache, indent=2))
            DEBUG_OUT.write_text(json.dumps(debug_all, indent=2))
            print(f'         [saved — {i} done]')

        time.sleep(0.3)

    print('\n[images] Running cross-school dedup …')
    manifest = cross_school_dedupe(manifest)

    MANIFEST.write_text(json.dumps(manifest, indent=2))
    SITES_FILE.write_text(json.dumps(site_cache, indent=2))
    DEBUG_OUT.write_text(json.dumps(debug_all, indent=2))

    print(f'[images] Manifest → {MANIFEST}')
    audit(manifest)


if __name__ == '__main__':
    args          = sys.argv[1:]
    force         = '--rebuild' in args
    school_filter = None
    sample        = None

    if '--school' in args:
        idx           = args.index('--school')
        school_filter = args[idx + 1]
    if '--sample' in args:
        idx    = args.index('--sample')
        sample = int(args[idx + 1])

    build(school_filter=school_filter, sample=sample, force=force)
