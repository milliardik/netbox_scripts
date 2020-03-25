'''
    Исходные данный список словарей:
        [
            - facts:
                fqdn: Elem_IK_AccSW_7.elem.ru
                hostname: Elem_IK_AccSW_7
                inventory:
                    - - WS-C2960-8TC-L
                      - FOC1532W3JC
                os_version: c2960-lanbasek9-mz.122-50.SE5.bin
                uptime: 36717300
                vendor: Cisco
              interfaces:
                Vlan301:
                    ipv4:
                        172.30.22.7:
                        prefix_length: 18
        ]
'''

import yaml
import pynetbox
import pprint
import ipaddress
from collections import namedtuple
from config import TOKEN, NETBOX_URL
from operator import attrgetter


def method_caller(name):
    def caller(obj, *args, **kwargs):
        return getattr(obj, name)(*args, **kwargs)
    return caller


default_roles = [
        dict(name='access_switch', slug='access_switch', color='52be80', vm_role=False),
        dict(name='distr_switch', slug='distr_switch', color='f39c12', vm_role=False),
        dict(name='core_switch', slug='core_switch', color='a93226', vm_role=False),
        dict(name='srv_switch', slug='srv_switch', color='85c1e9', vm_role=False),
    ]

method_get = method_caller('get')
method_all = method_caller('all')
method_filter = method_caller('filter')
method_create = method_caller('create')

attr_serial = attrgetter('serial')
attr_name = attrgetter('name')
Inventory = namedtuple('Inventory', ['manufacturer', 'model', 'serial', 'slug'])


pp = pprint.PrettyPrinter(width=80)
nb = pynetbox.api(url=NETBOX_URL, token=TOKEN)

obj_sites = nb.dcim.sites
obj_devices = nb.dcim.devices
obj_device_manufacturer = nb.dcim.manufacturers
obj_device_types = nb.dcim.device_types
obj_device_roles = nb.dcim.device_roles
obj_interfaces = nb.dcim.interfaces
obj_ipam_preficex = nb.ipam.prefixes
obj_ipam_ipaddreses = nb.ipam.ip_addresses
obj_ipam_vlans = nb.ipam.vlans
obj_vchassis = nb.dcim.virtual_chassis



'''
    Подготовка данных
    Получения из netbox списков 
        1. manufacturer - Бренд, по умолчанию используется устраства Cisco  
        2. site - принадлежность устройства к объекту. По умолачанию default
        3. Роли устройств
        4. Модели устройств
'''
try:
    nb_dsite = method_get(obj_sites, name='default').id # print(site) --> 3
except:
    nb_dsite = method_create(obj_sites, name='default', slug='default').id
try:
    nb_dmanufacturer = method_get(obj_device_manufacturer, name='Cisco').id # print(manufacturer) --> 1
except:
    nb_dmanufacturer = method_create(obj_device_manufacturer, name='Cisco', slug='cisco')

nb_droles = {rname.name: rname.id for rname in method_all(obj_device_roles) if rname.name.endswith('switch')}
if not nb_droles:
    for rname in method_create(obj_device_roles, default_roles):
        if rname.name.endswith('switch'):
            nb_droles.update({rname.name:rname.id})


nb_dtypes = {dt.model: dt.id for dt in method_all(obj_device_types)}
nb_prefixes = [p.prefix for p in method_all(obj_ipam_preficex)]
nb_vlans = {vlan.name: vlan.id for vlan in method_all(obj_ipam_vlans)}

with open('prepea_data.yaml') as f:
    devices = yaml.load(f, Loader=yaml.FullLoader)#[:2]

for d in devices:
    nb_devices = []
    dinventory = []
    dfacts = d['facts']
    hostname = dfacts['hostname'].lower()
    dinterfaces = d['interfaces']

    for model, serial in dfacts['inventory']:
        if not model.startswith(('WS-C45', 'WS-C65')):
            model = model[:model.rfind('-')]

        slug = model.replace('WS-', '').lower()
        dinventory.append(Inventory(nb_dmanufacturer, model, serial, slug))

    # Проверка есть ли текущая модель в netbox, если нет, то создать
    for inv in dinventory:
        if inv.model not in nb_dtypes:
            nb_dtypes[inv.model] = method_create(obj_device_types, **inv._asdict()).id

    # Получаем значение роль устройства
    # 'access_switch'
    # 'distr_switch'
    # 'core_switch'
    # 'srv_switch'
    if 'srv' in hostname:
        drole = 'srv_switch'
    elif 'distr' in hostname or 'bbsw' in hostname:
        drole = 'distr_switch'
    elif 'core' in hostname:
        drole = 'core_switch'
    else:
        drole = 'access_switch'


    # Создаем устройство
    vchassi_id = None
    vchassi = True if len(dinventory) > 1 else False

    for i in range(len(dinventory)):
        name = hostname + f'-{i+1}' if vchassi else hostname
        device = method_create(obj_devices,
                               name=name,
                               device_type=nb_dtypes[dinventory[i].model],
                               device_role=nb_droles[drole],
                               site=nb_dsite,
                               serial=dinventory[i].serial)

        if vchassi:
            if not i:
                vchassi_id = method_create(obj_vchassis, master=device.id).id

            device.virtual_chassis = vchassi_id
            device.vc_position = i+1
            device.save()
        nb_devices.append(device)

    for ifname, ipv4 in dinterfaces.items():
        if ifname.startswith('Vl'):
            type = 'virtual'
        elif ifname.startswith('Gi'):
            type = '1000base-x-sfp'
        elif ifname.startswith('Te'):
            type = '10gbase-x-sfpp'

        nb_interface = method_create(obj_interfaces,
                                     name=ifname,
                                     device=nb_devices[0].id,
                                     type=type,
                                     enabled=True)

        for i in ipv4['ipv4']:
            ip_address = ipaddress.IPv4Interface(i+f"/{ipv4['ipv4'][i]['prefix_length']}")
            pfx = str(ip_address.network)
            if pfx not in nb_prefixes and pfx[-2:] != '32':
                kwargs = dict(prefix=pfx, status='active', is_pool=False)
                if nb_interface.name.startswith('Vlan'):
                    vlan_name = nb_interface.name

                    if nb_interface.name not in nb_vlans:
                        vid = int(nb_interface.name.replace('Vlan', ''))
                        nb_vlans[vlan_name] = method_create(obj_ipam_vlans,
                                                            vid=vid,
                                                            name=vlan_name).id

                    kwargs['vlan'] = nb_vlans[vlan_name]

                nb_pfx = method_create(obj_ipam_preficex, **kwargs)
                nb_prefixes.append(nb_pfx.prefix)

            nb_ip_add = method_create(obj_ipam_ipaddreses,
                                      address=ip_address.with_netmask,
                                      interface=nb_interface.id)

            if (nb_interface.name.endswith(('130', '300', '301', '302', '303')) or
                    len(list(ipv4['ipv4'].keys())) == 1):
                nb_devices[0].primary_ip4 = nb_ip_add.id
                nb_devices[0].save()










