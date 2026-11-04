#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on December 2024

Developed and tested on:

- Linux 22.04 LTS
- Python 3.10 (Spyder 5)

@author: Nikola Knezevic ASTRO DATA
"""

import os
import requests
import json
import time
from collections import OrderedDict


#--------------------------------------------------- PARAMETERS --------------------------------------------------#
#tns = "www.wis-tns.org" # production
tns = "sandbox.wis-tns.org" # sandbox

url_tns_api = "https://" + tns + "/api"

tns_bot_id = "Here put your tns bot id."
tns_bot_name = "Here put your tns bot name."
tns_api_key = "Here put your tns api key."

# List that represents json file with all posible ("key", "value") parameters for searching obj on the tns
search_obj = [
              ("ra", ""), 
              ("dec", ""), 
              ("radius", ""), 
              ("units", ""), 
              ("objid", ""), 
              ("objname", ""), 
              ("objname_exact_match", 0), 
              ("internal_name", ""), 
              ("internal_name_exact_match", 0), 
              ("public_timestamp", ""), 
              ("reported_period_value", ""), 
              ("reported_period_units", "months"), 
              ("unclassified_at", "0"), 
              ("classified_sne", "0"),
              ("include_frb", "0"), 
              ("name", ""), 
              ("name_like", "0"), 
              ("isTNS_AT", "all"),
              ("public", "all"), 
              ("coords_unit", "arcsec"), 
              ("reporting_groupid", []),
              ("data_source_groupid", []), 
              ("classifier_groupid", []), 
              ("objtype", []), 
              ("at_type", []),
              ("discovery_date_start", ""), 
              ("discovery_date_end", ""), 
              ("discovery_mag_min", ""), 
              ("discovery_mag_max", ""), 
              ("discoverer", ""), 
              ("classifier", ""), 
              ("spectra_count", ""), 
              ("redshift_min", ""), 
              ("redshift_max", ""), 
              ("hostname", ""), 
              ("ext_catid", ""), 
              ("ra_range_min", ""), 
              ("ra_range_max", ""), 
              ("decl_range_min", ""), 
              ("decl_range_max", ""), 
              ("discovery_instrument", []), 
              ("classification_instrument", []), 
              ("associated_groups", []),
              ("official_discovery", ""), 
              ("official_classification", ""), 
              ("at_rep_remarks", ""),
              ("class_rep_remarks", ""), 
              ("frb_repeat", ""), 
              ("frb_repeater_of_objid", ""),
              ("frb_measured_redshift", ""), 
              ("frb_dm_range_min", ""), 
              ("frb_dm_range_max", ""),
              ("frb_rm_range_min", ""), 
              ("frb_rm_range_max", ""), 
              ("frb_snr_range_min", ""),
              ("frb_snr_range_max", ""), 
              ("frb_flux_range_min", ""), 
              ("frb_flux_range_max", "")
             ]

# Here you put ("key", "value") pairs for building your tns search list
build_search_obj = []

# List that represents json file with all posible ("key", "value") parameters for getting obj from the tns
get_obj = [
           ("objname", ""), 
           ("objid", ""), 
           ("photometry", "0"), 
           ("spectra", "1")
          ]

# Here you put ("key", "value") pairs for building your tns get list
build_get_obj = []

# File url (for downloading file from tns)
file_url = "Here put url of a file you want to download from TNS."

ext_http_errors = [403, 500, 503]
err_msg = ["Forbidden", "Internal Server Error: Something is broken", "Service Unavailable"]
#-----------------------------------------------------------------------------------------------------------------#


#--------------------------------------------------- FUNCTIONS ---------------------------------------------------#
def set_bot_tns_marker(bot_id, bot_name):
    tns_marker = 'tns_marker{"tns_id": "' + str(bot_id) + '", "type": "bot", "name": "' + bot_name + '"}'
    return tns_marker

def format_to_json(source):
    parsed = json.loads(source, object_pairs_hook = OrderedDict)
    result = json.dumps(parsed, indent = 4)
    return result

def is_string_json(string):
    try:
        json_object = json.loads(string)
    except Exception:
        return False
    return json_object

def print_status_code(response):
    json_string = is_string_json(response.text)
    if json_string != False:
        print ("status code ---> [ " + str(json_string['id_code']) + " - '" + json_string['id_message'] + "' ]\n")
    else:
        status_code = response.status_code
        if status_code == 200:
            status_msg = 'OK'
        elif status_code in ext_http_errors:
            status_msg = err_msg[ext_http_errors.index(status_code)]
        else:
            status_msg = 'Undocumented error'
        print ("status code ---> [ " + str(status_code) + " - '" + status_msg + "' ]\n")

def search(bot_id, bot_name, api_key, search_obj_list):
    search_url = url_tns_api + "/get/search"
    tns_marker = set_bot_tns_marker(bot_id, bot_name)
    headers = {'User-Agent': tns_marker}
    json_file = OrderedDict(search_obj_list)
    search_data = {'api_key': api_key, 'data': json.dumps(json_file)}
    response = requests.post(search_url, headers=headers, data=search_data)
    return response

def get(bot_id, bot_name, api_key, get_obj_list):
    get_url = url_tns_api + "/get/object"
    tns_marker = set_bot_tns_marker(bot_id, bot_name)
    headers = {'User-Agent': tns_marker}
    json_file = OrderedDict(get_obj_list)
    get_data = {'api_key': api_key, 'data': json.dumps(json_file)}
    response = requests.post(get_url, headers = headers, data = get_data)
    return response

def get_file(bot_id, bot_name, api_key, furl, file_dir):
    filename = os.path.basename(furl)
    tns_marker = set_bot_tns_marker(bot_id, bot_name)
    headers = {'User-Agent': tns_marker}
    api_data = {'api_key': api_key}
    print ("Downloading file '" + filename + "' from the TNS...\n")
    response = requests.post(furl, headers=headers, data=api_data, stream=True)    
    print_status_code(response)
    path = os.path.join(file_dir, filename)
    if response.status_code == 200:
        with open(path, 'wb') as f:
            for chunk in response:
                f.write(chunk)
        print ("File was successfully downloaded.\n")
    else:
        print ("File was not downloaded.\n")

def rate_limit_response(response, json_file, counter):
    response_code = str(response.status_code) if json_file == False else str(json_file['id_code'])
    if(response.headers.get('x-rate-limit-limit') != None):
        stats1 = ' | Total Rate-Limit: ' + str(response.headers.get('x-rate-limit-limit')) + \
                 ' | Remaining: ' + str(response.headers.get('x-rate-limit-remaining')) + \
                 ' | Reset: ' + str(response.headers.get('x-rate-limit-reset'))
    else:
        stats1 = ''
    if(response.headers.get('x-cone-rate-limit-limit') != None):
        stats2 = ' | Cone Rate-Limit: ' + str(response.headers.get('x-cone-rate-limit-limit')) + \
                 ' | Cone Remaining: ' + str(response.headers.get('x-cone-rate-limit-remaining')) + \
                 ' | Cone Reset: ' + str(response.headers.get('x-cone-rate-limit-reset'))
    else:
        stats2 = ''
    if (stats1 == '') and (stats2 == ''):
        print ("An error occurred while trying to test rate limits.")
        print ("There are no 'x-rate-limit-limit' and 'x-cone-rate-limit-limit' keywords "\
               "in the response.headers function.\nPlease check what went wrong.\n")
        return False
    else:
        stats = 'Test #' + str(counter) + '| return code: ' + response_code + stats1 + stats2
        print (stats)
        return True

def get_reset_time(response):
    # If any of the '...-remaining' values is zero, return the reset time
    for name in response.headers:
        value = response.headers.get(name)
        if name.endswith('-remaining') and value == '0':
            return int(response.headers.get(name.replace('remaining', 'reset')))
    return None

def rate_limit_handling(bot_id, bot_name, api_key, search_obj_list):
    counter = 0
    while True:
        counter = counter + 1
        response = search(bot_id, bot_name, api_key, search_obj_list)
        json_file = is_string_json(response.text)
        r = rate_limit_response(response, json_file, counter)
        if r == False:
            break
        # Checking if rate-limit reached (...-remaining = 0)
        reset = get_reset_time(response)
        # A general verification if not some error 
        if (response.status_code == 200):
            if reset != None:
                # Sleeping for reset + 1 sec
                print("Sleep for " + str(reset + 1) + " sec") 
                time.sleep(reset + 1)
        	    # Can continue to submit requests...
                print ("Continue to submit requests...")
                for i in range(3):
                    if r == True:
                        counter = counter + 1
                        response = search(bot_id, bot_name, api_key, search_obj_list)
                        json_file = is_string_json(response.text)
                        r = rate_limit_response(response, json_file, counter)
                if r == True:
                    print ("etc...\n")
                break
        else:
            print_status_code(response)       
            break
#-----------------------------------------------------------------------------------------------------------------#


#--------------------------------------------------- EXAMPLES ----------------------------------------------------#
cwd  = os.getcwd()
get_file_dir = os.path.join(cwd, "get_files")

# Comment/Uncomment sections for testing the various examples:

"""
# EXAMPLE 1 (search obj)
build_search_obj = [("ra", "00:25:29.960"), ("dec", "+20:14:34.51")]
response = search(tns_bot_id, tns_bot_name, tns_api_key, build_search_obj)
json_data = format_to_json(response.text)
print (json_data)
"""

"""
# EXAMPLE 2 (get obj)
build_get_obj = [("objname", "2024ryv"), ("spectra", "1")]
response = get(tns_bot_id, tns_bot_name, tns_api_key, build_get_obj)
json_data = format_to_json(response.text)
print (json_data)
"""

"""
# EXAMPLE 3 (get file from TNS)
file_url = "https://" + tns + "/system/files/uploaded/"\
           "LiONS/tns_2024ryv_2024-08-13_21-03-54.954_Lijiang-2.4m_YFOSC_LiONS.dat"
get_file(tns_bot_id, tns_bot_name, tns_api_key, file_url, get_file_dir)
"""

"""
# EXAMPLE 4 (test rate-limit search)
build_search_obj = [("objname", "2024see")]
rate_limit_handling(tns_bot_id, tns_bot_name, tns_api_key, build_search_obj)
"""

"""
# EXAMPLE 5 (test rate-limit cone search)
build_search_obj = [("ra", "15:57:28"), ("dec", "+30:03:39"), ("radius", "5"), ("units", "arcsec")]
rate_limit_handling(tns_bot_id, tns_bot_name, tns_api_key, build_search_obj)
"""
#-----------------------------------------------------------------------------------------------------------------#



