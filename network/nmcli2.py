#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2015 Ales Zelenik <ales.zelenik@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: nmcli2
short_description: Configure NetworkManager via nmcli
description:
    - Manages network configuration settings on systems utilizing NetworkManager se via nmcli frontend.
    - Allows setting all NetworkManager supported properties.
version_added: "2.0"
author: Ales Zelenik (https://github.com/alesz/)
notes:
    - null
requirements:
    - [ nmcli ]
options:
    state:
        description:
            - Ensures interface state by creating, applying changes, deleting or shutting it down.
            - 'up' state is synonymous to 'present'.
            - Interface in 'down' state will still check and apply parameters if they differ from definition.
        required: true
        choices: [ 'present', 'up', 'absent', 'down' ]
        version_added: 2.0
    name:
        description:
            - References connection.id property. Changing it will manage different interface, be careful.
        required: true
        version_added: 2.0
    type:
        description:
            - Type of connection to create. Changing type will recreate the interface, if it already exists.
            - Valid connection types: generic, 802-3-ethernet (ethernet), pppoe, 802-11-wireless (wifi), wimax, gsm,
            - cdma, infiniband, adsl, bluetooth, vpn, 802-11-olpc-mesh (olpc-mesh), vlan, bond, team, bridge,
            - bond-slave, team-slave, bridge-slave
        required: true
        choices: [ 'generic', '802-3-ethernet', 'ethernet', 'pppoe', '802-11-wireless', 'wifi', 'wimax', 'gsm',
                   'cdma', 'infiniband', 'adsl', 'bluetooth', 'vpn', '802-11-olpc-mesh', 'olpc-mesh', 'vlan', 'bond',
                   'team', 'bridge', 'bond-slave', 'team-slave', 'bridge-slave' ]
        version_added: 2.0
    properties:
        description:
            - Dictionary of mandatory and optional properties that are relevant to connection type.
            - Reference output of 'nmcli con show NAME' for configurable properties.
            - Cannot be used to modify following properties: connection.id, connection.uuid, connection.type
        required: false
        version_added: 2.0
'''
EXAMPLES = '''
- hosts: my-host
  vars:
    eth_config:
      connection.interface-name: enp0s25
      ipv4.method: manual
      ipv4.addresses: 172.20.20.20/24
      ipv4.gateway: 172.20.20.1
  tasks:
  # ---- Installs and enables NetworkManager ----
  - name: Install NetworkManager
    apt: name={{ item }} state=latest
    with_items:
      - network-manager
  - lineinfile: dest=/etc/NetworkManager/NetworkManager.conf regexp=^managed= line=managed=true
  - service: name=network-manager enabled=yes state=started
  # ---- Installs and enables NetworkManager ----
  - name: Configure static IPv4 address with default gateway on interface eth1 and activate it
    nmcli2: name=Connection type=ethernet state=present properties='{{ eth_config }}'
  - name: Connect to wifi with WPA PSK
    nmcli2: name=MyWifi type=wifi state=present properties="wifi.ssid=MySSID, 802-11-wireless-security.psk=mysecret"
  - name: Create bridge
    nmcli2: name=br-vxlan type=bridge state=present
      properties="connection.interface-name=br-vxlan,
                  bridge.stp=no,
                  ipv4.method=manual,
                  ipv4.addresses=172.29.240.100/22,
                  ipv6.method=ignore"
  - name: Create vlan 500 and assign it to bridge br-vxlan
    nmcli2: name=vlan500 type=vlan state=present
      properties="connection.interface-name=vlan500, vlan.parent=eth1, vlan.id=500, connection.master=br-vxlan, connection.slave-type=bridge"
  - name: Create LACP negotiated port channel
    nmcli2: name=bondLACP type=bond state=present
      properties="connection.interface-name=bond0,
                  mode=802.3ad,
                  miimon=200,
                  downdelay=50,
                  updelay=50,
                  ipv4.method=manual,
                  ipv4.addresses=10.1.2.3/24
  - name: Assign interfaces to bond0
    nmcli2: name={{ item }} type=ethernet state=present
      properties="connection.interface-name={{ item }},
                  connection.master=bond0,
                  connection.slave-type=bond"
   with_items:
     - eth0
     - eth1
'''

try:
    import re
    import itertools
    HAS_LIB = True
except:
    HAS_LIB = False


class NMCli(object):
    def __init__(self, module):
        self.module = module
        if self.module.params['type'] in ['bridge-slave']:
            self.module.fail_json(
                msg="Use connection.master and connection.slave-type parameters on the interface instead of *-slave type")
        # normalize type names and properties
        if self.module.params['type'] == 'wifi':
            self.module.params['type'] = '802-11-wireless'
        if self.module.params['type'] == 'ethernet':
            self.module.params['type'] = '802-3-ethernet'
        for key in self.module.params['properties']:
            if key.startswith('wifi.'):
                self.module.params['properties'][key.replace('wifi.', '802-11-wireless.', 1)] = self.module.params[
                    'properties'].pop(key)
            elif key.startswith('ethernet.'):
                self.module.params['properties'][key.replace('ethernet.', '802-3-ethernet.', 1)] = self.module.params[
                    'properties'].pop(key)
        # path to nmcli or fail if not found
        self.nmcli = self.module.get_bin_path('nmcli', True)

    def con_exist_and_type(self):
        cmd = [self.nmcli, '-t', '-m', 'tab', '--fields', 'connection.type,GENERAL.STATE', 'con', 'show',
               self.module.params['name']]
        (rc, out, err) = self.module.run_command(cmd)
        exist = False
        is_active = False
        is_type = False
        if rc == 10:
            # Connection, device, or access point does not exist.
            pass
        elif rc == 0:
            exist = True
            o = re.split(r'\n', out.strip())
            type = o[0]
            # Connection active or activating
            if len(o) == 2 and 'activ' in o[1]:
                is_active = True
            if type in self.module.argument_spec['type']['choices'] and self.module.params['type'] == type:
                is_type = True
        else:
            self.module.fail_json(msg=err)
        return exist, is_type, is_active

    def _con_show(self):
        show = {}
        cmd = [self.nmcli, '-t', '-m', 'multi', '--fields', 'common', 'con', 'show']
        if self.module.params['type'] == '802-11-wireless':
            cmd.append('--show-secrets')
        cmd.append(self.module.params['name'])
        (rc, out, err) = self.module.run_command(cmd)
        if rc == 0:
            for prop in re.split(r'[\n]', out):
                pair = re.split(r'[:]', prop, 1)
                if len(pair) == 2:
                    show[pair[0]] = pair[1]
            return show
        else:
            self.module.fail_json(msg=err)

    def con_mod_diff(self):
        diff_cmd = []
        cmd = [self.nmcli, 'con', 'mod', self.module.params['name']]
        show = self._con_show()
        prop = self.module.params['properties']
        intersect = set(show.keys()) & set(prop.keys())
        unknown = set(prop.keys()) - intersect
        if len(unknown) > 0:
            self.module.fail_json(
                msg="Following properties are not recognized by specified type: %s" % (" ".join(unknown)))
        for k in intersect:
            # handle different output TODO improve
            if prop[k] != show[k]:
                if k == 'ipv4.addresses' and prop[k] in show[k]:
                    continue
                diff_cmd.append([k, prop[k]])

        if len(diff_cmd) == 0 or self.module.check_mode:
            return diff_cmd

        # if cmd contains connection.master or connection.slave-type,
        # run slave-type separately (works for nm 0.9) TODO simplify code?
        pending = list(cmd)
        i = 0
        cmd += list(itertools.chain.from_iterable(diff_cmd))
        if 'connection.master' in cmd and 'connection.slave-type' in cmd and self.module.params['state'] in ['present',
                                                                                                             'up']:
            i = cmd.index('connection.slave-type')
            pending.append(cmd.pop(i))
            pending.append(cmd.pop(i))
        (rc, out, err) = self.module.run_command(cmd)
        if rc == 0:
            if i != 0:
                (rc, out, err) = self.module.run_command(pending)
                if rc == 0:
                    return diff_cmd
                else:
                    self.module.fail_json(msg=err)
            return diff_cmd
        else:
            self.module.fail_json(msg=err)

    def con_add(self):
        if self.module.check_mode:
            self.module.exit_json(changed=True)
        args = self._props_to_args()
        if self.module.params['type'] in ['wifi', '802-11-wireless']:
            if not self.module.params['properties'].has_key('802-11-wireless.ssid'):
                self.module.fail_json(msg="802-11-wireless.ssid property is required for wireless connections")
            cmd = [self.nmcli, 'dev', 'wifi', 'connect', self.module.params['properties']['802-11-wireless.ssid'],
                   'name', self.module.params['name']]
            cmd.append('password')
            # add psk key to prevent prompting the user
            if not self.module.params['properties'].has_key('802-11-wireless-security.psk'):
                cmd.append('INVALID')
            else:
                cmd.append(self.module.params['properties']['802-11-wireless-security.psk'])
        else:
            cmd = [self.nmcli, 'con', 'add', 'con-name', self.module.params['name'], 'type', self.module.params['type']]
            for item in args.keys():
                cmd.append(item)
                cmd.append(args[item])
        (rc, out, err) = self.module.run_command(cmd)
        if rc != 0:
            # TODO if error msg looks like "Error: 'id' is required.\n" replace arg name with parameter name
            self.module.fail_json(msg=err)

    add_map = {'connection.interface-name': 'ifname',
               'wifi.ssid': 'ssid',
               '802-11-wireless.ssid': 'ssid',
               'pppoe.username': 'username',
               'gsm.apn': 'apn',
               'bluetooth.bdaddr': 'addr',
               'vlan.parent': 'dev',
               'vlan.id': 'id',
               'vpn.service-type': 'vpn-type',  # TODO normalize org.freedesktop.NetworkManager.pptp
               '802-11-olpc-mesh': 'ssid',
               'olpc-mesh': 'ssid'}

    add_only = ('nsp')  # TODO handle wimax special snowflake where property for nsp does not exist

    def _props_to_args(self):
        ret = {}
        # rename keys based on add_map for use with add command
        for add_key in self.add_map.keys():
            if self.module.params['properties'].has_key(add_key):
                ret[self.add_map[add_key]] = self.module.params['properties'][add_key]
        return ret

    def con_delete(self):
        if self.module.check_mode:
            return
        cmd = [self.nmcli, 'con', 'del', self.module.params['name']]
        (rc, out, err) = self.module.run_command(cmd)
        if rc != 0:
            self.module.fail_json(msg=err)

    def con_active(self, activate):
        if self.module.check_mode:
            return
        cmd = [self.nmcli, 'con', activate and 'up' or 'down', self.module.params['name']]
        (rc, out, err) = self.module.run_command(cmd)
        if rc != 0:
            self.module.fail_json(msg=err)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(required=True, choices=['present', 'up', 'absent', 'down'], type='str'),
            name=dict(required=True, type='str'),
            type=dict(required=True, choices=['generic', '802-3-ethernet', 'ethernet', 'pppoe', '802-11-wireless',
                                              'wifi', 'wimax', 'gsm', 'cdma', 'infiniband', 'adsl', 'bluetooth', 'vpn',
                                              '802-11-olpc-mesh', 'olpc-mesh', 'vlan', 'bond', 'team', 'bridge',
                                              'bond-slave', 'team-slave', 'bridge-slave'], type='str'),
            properties=dict(required=False, type='dict', default=dict())
        ),
        supports_check_mode=True
    )
    if not HAS_LIB:
        module.fail_json(msg="Failed to import required libs re or itertools")

    nmcli = NMCli(module)
    changed = False

    if module.params['state'] in ['present', 'up', 'down']:
        (exist, is_type, is_active) = nmcli.con_exist_and_type()
        if exist and not is_type:
            nmcli.con_delete()
            exist = is_active = False
        if not exist:
            nmcli.con_add()
            changed = True
            (exist, is_type, is_active) = nmcli.con_exist_and_type()
        diff = nmcli.con_mod_diff()
        if len(diff) > 0:
            changed = True
        if module.params['state'] in ['down'] and is_active:
            nmcli.con_active(False)
            changed = True
        elif not module.params['state'] in ['down'] and not is_active:
            nmcli.con_active(True)
            changed = True
    elif module.params['state'] in ['absent']:
        (exist, is_type, is_active) = nmcli.con_exist_and_type()
        if exist:
            nmcli.con_delete()
            changed = True

    module.exit_json(changed=changed)


from ansible.module_utils.basic import *

if __name__ == '__main__':
    main()
