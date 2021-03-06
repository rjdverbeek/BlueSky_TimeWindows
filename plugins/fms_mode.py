""" Flight Management System Mode plugin """
# Import the global bluesky objects. Uncomment the ones you need
from datetime import date, datetime, time, timedelta
# from math import sqrt
import numpy as np

from bluesky import sim, stack, traf, tools  #, settings, navdb, sim, scr, tools
from bluesky.traffic.route import Route
from bluesky.traffic.performance.legacy.performance import PHASE
# import inspect  # TODO Remove after test

# Global data
afms = None

#TODO Implement idea from Johannes to have updates at the waypoints, on an interval, reseting interal at waypoint

def init_plugin():

    # Additional initilisation code
    global afms, acceleration_m_s2, deceleration_m_s2
    acceleration_m_s2 = 0.5
    deceleration_m_s2 = -0.5

    afms = Afms()

    # Configuration parameters
    config = {
        # The name of your plugin
        'plugin_name':     'AFMS',

        # The type of this plugin. For now, only simulation plugins are possible.
        'plugin_type':     'sim',

        # Update interval in seconds. By default, your plugin's update function(s)
        # are called every timestep of the simulation. If your plugin needs less
        # frequent updates provide an update interval.
        'update_interval': afms.dt,

        # The update function is called after traffic is updated. Use this if you
        # want to do things as a result of what happens in traffic. If you need to
        # something before traffic is updated please use preupdate.
        'update':          afms.update,

        # The preupdate function is called before traffic is updated. Use this
        # function to provide settings that need to be used by traffic in the current
        # timestep. Examples are ASAS, which can give autopilot commands to resolve
        # a conflict.
        'preupdate':       afms.preupdate,

        # If your plugin has a state, you will probably need a reset function to
        # clear the state in between simulations.
        'reset':         afms.reset
        }

    stackfunctions = {
        # The command name for your function
        'AFMS_FROM': [
            # A short usage string. This will be printed if you type HELP <name> in the BlueSky console
            'acid AFMS_FROM wpinroute CONTINUE/OFF/OWN/RTA/TW',

            # A list of the argument types your function accepts. For a description of this, see ...
            'acid,wpinroute,txt',

            # The name of your function in this plugin
            afms.afms_from,

            # a longer help text of your function.
            'AFMS_FROM command that sets the mode of the Advanced FMS from a specified waypoint.'
            'The choice is between: CONTINUE (continue with current advanced FMS mode), OFF (turn advanced FMS off ),'
            'RTA (aim to arrive just in time at the next RTA time constraint), and TW (aim to arrive within the'
            'time window at the next Time Window constraint). In case of OWN/TW mode you must specify additionally'
            'the own speed (=preferred speed) using the OWN_SPEED_FROM command'],
        'RTA_AT': [
            # A short usage string. This will be printed if you type HELP <name> in the BlueSky console
            'acid RTA_AT wpname HH:MM:SS',

            # A list of the argument types your function accepts. For a description of this, see ...
            'acid, wpinroute, txt',

            # The name of your function in this plugin
            afms.rta_at,

            # a longer help text of your function.
            'RTA_AT command that sets the time at which the aircraft should arrive at the'
            'specified waypoint. The time constraint is only used if the AFMS mode is set accordingly to RTA/TW'],
        'TW_SIZE_AT': [
            # A short usage string. This will be printed if you type HELP <name> in the BlueSky console
            'acid TW_SIZE_AT wpname time_window_size',

            # A list of the argument types your function accepts. For a description of this, see ...
            'acid, wpinroute, int',

            # The name of your function in this plugin
            afms.tw_size_at,

            # a longer help text of your function.
            'TW_SIZE_AT command  sets the time window size in seconds for the aircraft arriving to the'
            'specified waypoint. The time constraint is only used if the AFMS mode is set accordingly to TW'],
        'RTWA_AT': [
            # A short usage string. This will be printed if you type HELP <name> in the BlueSky console
            'acid RTWA_AT wpname time_window_size HH:MM:SS ',

            # A list of the argument types your function accepts. For a description of this, see ...
            'acid, wpinroute, int, txt',

            # The name of your function in this plugin
            afms.rtwa_at,

            # a longer help text of your function.
            'RTWA_AT command that sets the time and time window size at which the aircraft should arrive at the'
            'specified waypoint. The time constraint is only used if the AFMS mode is set accordingly to RTA/TW'],
        'OWN_SPD_FROM': [
            # A short usage string. This will be printed if you type HELP <name> in the BlueSky console
            'acid OWN_SPD_FROM wpname,[spd]',

            # A list of the argument types your function accepts. For a description of this, see ...
            'acid,wpinroute,[spd]',

            # The name of your function in this plugin
            afms.own_from,

            # a longer help text of your function.
            'OWN_SPD_FROM command that sets the own speed to use for the specific aircraft from a '
            'specific waypoint. The Own Speed is only used for the OWN mode / TW mode.'
            'If no speed is given the BADA reference speed is used.'],
    }

    # init_plugin() should always return these two dicts.
    return config, stackfunctions


### Periodic update functions that are called by the simulation. You can replace
### this by anything, so long as you communicate this in init_plugin

class Afms:
    """ Advanced FMS: dynamically adjust speed of flights based on set AFMS mode and/or RTA/Time Window"""
    def __init__(self):
        super(Afms, self).__init__()
        # Parameters of afms
        self.dt = 60.0  # [s] frequency of afms update (simtime)
        self.skip2next_rta_time_s = 300.0  # Time when skipping to the RTA beyond the active RTA
        self.rta_standard_window_size = 60.  # [s] standard time window size for rta in seconds
        self._patch_route(self.rta_standard_window_size)
        self._fms_modes = ['OFF', 'CONTINUE', 'OWN', 'RTA', 'TW']

    @staticmethod
    def _patch_route(rta_standard_window_size):  #
        """
        Patch the route class to allow for new data types needed for AFMS.
        These include wprta, wprta_window_size, wpfms_mode, and wpown.
        wprta indicates the rta time
        wprta_window_size gives the time window size in seconds
        wpfms_mode gives the used afms mode
        wpown gives the preferred speed (Mach or CAS in m/s)
        """
        Route._rta_standard_window_size = rta_standard_window_size
        old_route_init = Route.__init__

        def new_route_init(self, *k, **kw):
            old_route_init(self, *k, **kw)
            self.wprta = []  # [s] Required Time of Arrival to WPT
            self.wprta_window_size = []  # [s] Window size around RTA
            self.wpfms_mode = []  # Advanced FMS mode
            self.wpown = []  # Own speed for TW/OWN Advanced FMS mode

        Route.__init__ = new_route_init

        old_route_addwpt_data = Route.addwpt_data

        def new_route_addwpt_data(self, overwrt, wpidx, *k, **kw):
            old_route_addwpt_data(self, overwrt, wpidx, *k, **kw)
            if overwrt:
                self.wprta[wpidx] = -1.  # negative indicates no rta
                self.wprta_window_size[wpidx] = Route._rta_standard_window_size
                self.wpfms_mode[wpidx] = 1  # Set advanced FMS mode to continue
                self.wpown[wpidx] = -1  # Set own spd index to use previous setting
            else:
                self.wprta.insert(wpidx, -1.)  # negative indicates no rta
                self.wprta_window_size.insert(wpidx, Route._rta_standard_window_size)
                self.wpfms_mode.insert(wpidx, 1)  # Set advanced FMS mode to continue
                self.wpown.insert(wpidx, -1)  # Set own speed index to use previous own speed setting

        Route.addwpt_data = new_route_addwpt_data

        old_route_del_wpt_data = Route._del_wpt_data

        def new_del_wpt_data(self, wpidx):
            old_route_del_wpt_data(self, wpidx)
            del self.wprta[wpidx]
            del self.wprta_window_size[wpidx]
            del self.wpfms_mode[wpidx]
            del self.wpown[wpidx]

        Route._del_wpt_data = new_del_wpt_data

    def update(self):  #
        pass

    def preupdate(self):  #
        """
        update the AFMS mode settings before the traffic is updated.
        """
        for idx, _ in enumerate(traf.id):
            fms_mode = self._current_fms_mode(idx)
            if int(traf.perf.phase[idx]) == PHASE['CR']:
                # if traf.ap.route[idx].iactwp < 0:
                #     pass
                if traf.actwp.next_qdr[idx] < -361 :
                    pass
                elif fms_mode == 0:  # AFMS_MODE OFF
                    pass
                elif fms_mode == 1:  # AFMS_MODE CONTINUE
                    pass
                elif fms_mode == 2:  # AFMS_MODE OWN
                    own_spd = self._current_own_spd(idx)
                    if own_spd < 0:
                        # print('No own speed specified')
                        pass  # Don't do anything
                    elif own_spd < 1:
                        if abs(traf.M[idx] - own_spd) > 0.0003:
                            stack.stack(f'SPD {traf.id[idx]}, {own_spd}')
                            stack.stack(f'VNAV {traf.id[idx]} ON')
                        else:
                            pass
                    else:
                        if abs(traf.cas[idx] - own_spd) > 0.1:
                            stack.stack(f'SPD {traf.id[idx]}, {own_spd * 3600 / 1852}')
                            stack.stack(f'VNAV {traf.id[idx]} ON')
                        else:
                            pass
                elif fms_mode == 3:  # AFMS_MODE RTA
                    rta_init_index, rta_last_index, rta = self._current_rta(idx)
                    time_s2rta = self._time_s2rta(rta)
                    if time_s2rta < self.skip2next_rta_time_s:
                        # Don't give any speed instructions in the last section (Keep stable speed)
                        # rta_init_index, rta_last_index, rta = self._current_rta_plus_one(idx)
                        # time_s2rta = self._time_s2rta(rta)
                        pass
                    else:
                        _, dist2nwp = tools.geo.qdrdist(traf.lat[idx], traf.lon[idx],
                                                        traf.ap.route[idx].wplat[rta_init_index],
                                                        traf.ap.route[idx].wplon[rta_init_index])
                        distances_nm = np.concatenate((np.array([dist2nwp]),
                                                    traf.ap.route[idx].wpdistto[rta_init_index + 1:rta_last_index + 1]),
                                                   axis=0)
                        flightlevels_m = np.concatenate((np.array([traf.alt[idx]]),
                                                       traf.ap.route[idx].wpalt[rta_init_index + 1:rta_last_index + 1]))
                        # rta_cas_kts = self._rta_cas_wfl(distances_nm, flightlevels_m, time_s2rta, traf.cas[idx]) * 3600 / 1852
                        rta_cas_m_s = self._cas2rta(distances_nm, flightlevels_m, time_s2rta, traf.cas[idx])
                        if abs(traf.cas[idx] - rta_cas_m_s) > 0.5:  # Don't give very small speed changes
                            if abs(traf.vs[idx]) < 2.5:  # Don't give a speed change when changing altitude
                                if tools.aero.vcas2mach(rta_cas_m_s, flightlevels_m[0]) > 0.95:
                                    stack.stack(f'SPD {traf.id[idx]}, {0.95}')
                                elif flightlevels_m[0] > 7620:
                                    stack.stack(f'SPD {traf.id[idx]}, {tools.aero.vcas2mach(rta_cas_m_s, flightlevels_m[0])}')
                                else:
                                    stack.stack(f'SPD {traf.id[idx]}, {rta_cas_m_s * 3600 / 1852}')
                                stack.stack(f'VNAV {traf.id[idx]} ON')
                        else:
                            pass
                elif fms_mode == 4:  # AFMS_MODE TW
                    rta_init_index, rta_last_index, rta = self._current_rta(idx)
                    tw_init_index, tw_last_index, tw_size = self._current_tw_size(idx)
                    time_s2rta = self._time_s2rta(rta)

                    _, dist2nwp = tools.geo.qdrdist(traf.lat[idx], traf.lon[idx],
                                                    traf.ap.route[idx].wplat[rta_init_index],
                                                    traf.ap.route[idx].wplon[rta_init_index])
                    distances_nm = np.concatenate((np.array([dist2nwp]),
                                                traf.ap.route[idx].wpdistto[rta_init_index + 1:rta_last_index + 1]),
                                               axis=0)
                    flightlevels_m = np.concatenate((np.array([traf.alt[idx]]),
                                                   traf.ap.route[idx].wpalt[rta_init_index + 1:rta_last_index + 1]))

                    # Calculate Preferred Speed and Preferred Time of Arrival
                    own_spd = self._current_own_spd(idx)
                    if own_spd < 0:
                        # No speed specified.
                        if traf.selspd[idx] > 0:  # Use selected speed
                            _, preferred_cas_m_s, _ = tools.aero.vcasormach(traf.selspd[idx], traf.alt[idx])
                        else:  # Use current speed
                            preferred_cas_m_s = traf.cas[idx]
                    else:
                        _, preferred_cas_m_s, _ = tools.aero.vcasormach(own_spd, traf.alt[idx])

                    eta_s_preferred = self._eta2tw_cas_wfl(distances_nm, flightlevels_m, preferred_cas_m_s)

                    # Jump to next +1 waypoint when close to next waypoint
                    if eta_s_preferred < self.skip2next_rta_time_s:
                        rta_init_index, rta_last_index, rta = self._current_rta_plus_one(idx)
                        #Don't change the time window size yet. Keep the current window size
                        time_s2rta = self._time_s2rta(rta)
                        _, dist2nwp = tools.geo.qdrdist(traf.lat[idx], traf.lon[idx],
                                                        traf.ap.route[idx].wplat[rta_init_index],
                                                        traf.ap.route[idx].wplon[rta_init_index])
                        distances_nm = np.concatenate((np.array([dist2nwp]),
                                                    traf.ap.route[idx].wpdistto[rta_init_index + 1:rta_last_index + 1]),
                                                   axis=0)
                        flightlevels_m = np.concatenate((np.array([traf.alt[idx]]),
                                                       traf.ap.route[idx].wpalt[rta_init_index + 1:rta_last_index + 1]))

                        eta_s_preferred = self._eta2tw_cas_wfl(distances_nm, flightlevels_m, preferred_cas_m_s)
                    else:
                        pass

                    earliest_time_s2rta = max(time_s2rta - tw_size/2, 0)
                    latest_time_s2rta = max(time_s2rta + tw_size/2, 0)

                    if eta_s_preferred < earliest_time_s2rta:  # Prefer earlier then TW
                        time_window_cas_m_s = self._cas2rta(distances_nm, flightlevels_m, earliest_time_s2rta,
                                                            traf.cas[idx])
                    elif eta_s_preferred > latest_time_s2rta:  # Prefer later then TW
                        time_window_cas_m_s = self._cas2rta(distances_nm, flightlevels_m, latest_time_s2rta,
                                                            traf.cas[idx])
                    else:
                        time_window_cas_m_s = preferred_cas_m_s

                    if abs(traf.cas[idx] - time_window_cas_m_s) > 0.5:  # Don't give very small speed changes
                        if abs(traf.vs[idx]) < 2.5:  # Don't give a speed change when changing altitude
                            if tools.aero.vcas2mach(time_window_cas_m_s, flightlevels_m[0]) > 0.95:
                                stack.stack(f'SPD {traf.id[idx]}, {0.95}')
                            elif flightlevels_m[0] > 7620:
                                stack.stack(f'SPD {traf.id[idx]}, {tools.aero.vcas2mach(time_window_cas_m_s, flightlevels_m[0])}')
                            else:
                                stack.stack(f'SPD {traf.id[idx]}, {time_window_cas_m_s * 3600 / 1852}')
                            stack.stack(f'VNAV {traf.id[idx]} ON')
                    else:
                        pass
                else:
                    return False, 'AFMS mode does not exist' + traf.id[idx]
            else:
                pass

    def reset(self):  #
        pass

    def _current_fms_mode(self, idx):  #
        """
        Identify active afms mode
        :param idx: aircraft index
        :return: fms mode
        """
        fms_mode_index = next((index for index, value in reversed(list(enumerate(traf.ap.route[idx].wpfms_mode[
                                                                                 :traf.ap.route[idx].iactwp])))
                               if value != 1), -1)
        if fms_mode_index < 0:
            return 0  # FMS MODE OFF
        else:
            return traf.ap.route[idx].wpfms_mode[:traf.ap.route[idx].iactwp][fms_mode_index]

    def _current_rta(self, idx):  #
        """
        Identify active rta
        :param idx: aircraft index
        :return: initial index rta, last index rta, rta: active rta
        """
        rta_index = next((index for index, value in enumerate(traf.ap.route[idx].wprta[traf.ap.route[idx].iactwp:]) if
                          isinstance(value, time)), -1)
        rta = traf.ap.route[idx].wprta[traf.ap.route[idx].iactwp:][rta_index] if rta_index > -1 else -1
        if rta_index > -1:
            return traf.ap.route[idx].iactwp, traf.ap.route[idx].iactwp + rta_index, rta
        else:
            return -1, -1, rta

    def _current_rta_plus_one(self, idx):  #
        """
        Identify rta beyond active rta
        :param idx: aircraft index
        :return: initial index active rta, last index rta beyond activate rta, rta: rta beyond activate rta
        """
        init_index_rta, last_index_active_rta, active_rta = self._current_rta(idx)
        beyond_rta_index = next((index for index, value in enumerate(traf.ap.route[idx].wprta[last_index_active_rta+1:]) if
                          isinstance(value, time)), -1)
        beyond_rta = traf.ap.route[idx].wprta[last_index_active_rta+1:][beyond_rta_index] if beyond_rta_index > -1 else -1
        if beyond_rta_index > -1:
            return init_index_rta, last_index_active_rta + 1 + beyond_rta_index, beyond_rta
        else:
            return init_index_rta, last_index_active_rta, active_rta


    def _current_own_spd(self, idx):  #
        """
        Identify active own Mach or CAS
        :param idx: aircraft index
        :return: Mach or CAS (or -1 in no speed is specified)
        """
        own_spd_index = next((index for index, value in reversed(list(enumerate(traf.ap.route[idx].wpown[
                                                                                 :traf.ap.route[idx].iactwp])))
                               if value >= 0), -1)
        if own_spd_index < 0:
            return -1  # NO OWN SPEED SPECIFIED
        else:
            return traf.ap.route[idx].wpown[:traf.ap.route[idx].iactwp][own_spd_index]

    def _current_tw_size(self, idx):  #
        """
        Identify active time window size
        :param idx: aircraft index
        :return: initial index tw, last index tw, time_window_size: active tw
        """
        rta_index = next((index for index, value in enumerate(traf.ap.route[idx].wprta[traf.ap.route[idx].iactwp:]) if
                          isinstance(value, time)), -1)
        tw_size = traf.ap.route[idx].wprta_window_size[traf.ap.route[idx].iactwp:][rta_index] if rta_index > -1 else 60.0
        if rta_index > -1:
            return traf.ap.route[idx].iactwp, traf.ap.route[idx].iactwp + rta_index, tw_size
        else:
            return -1, -1, tw_size

    def afms_from(self, idx, *args):  #
        if len(args) < 2 or len(args) > 3:
            return False, 'AFMS_AT needs three or four arguments'
        else:
            name = args[0]
            mode = args[1]
            if name in traf.ap.route[idx].wpname:
                wpidx = traf.ap.route[idx].wpname.index(name)
                if mode in self._fms_modes:
                    if mode == self._fms_modes[0]:
                        traf.ap.route[idx].wpfms_mode[wpidx] = 0
                    elif mode == self._fms_modes[2]:
                        traf.ap.route[idx].wpfms_mode[wpidx] = 2
                    elif mode == self._fms_modes[3]:
                        traf.ap.route[idx].wpfms_mode[wpidx] = 3
                    elif mode == self._fms_modes[4]:
                        traf.ap.route[idx].wpfms_mode[wpidx] = 4
                    else:
                        traf.ap.route[idx].wpfms_mode[wpidx] = 1  # CONTINUE
                else:
                    return False, mode + 'does not exist' + traf.id[idx]
            else:
                return False, name + 'not found in route' + traf.id[idx]

    def rta_at(self, idx, *args):  #
        if len(args) != 2:
            return False, 'RTA_AT needs 3 arguments'
        else:
            name = args[0]
            rta_time = args[1]
            if name in traf.ap.route[idx].wpname:
                wpidx = traf.ap.route[idx].wpname.index(name)
                traf.ap.route[idx].wprta[wpidx] = datetime.strptime(rta_time, '%H:%M:%S').time()
            else:
                return False, name + 'not found in route' + traf.id[idx]

    def tw_size_at(self, idx, *args):  #
        if len(args) != 2:
            return False, 'TW_SIZE_AT needs 3 arguments'
        else:
            name = args[0]
            tw_size = args[1]
            if name in traf.ap.route[idx].wpname:
                wpidx = traf.ap.route[idx].wpname.index(name)
                traf.ap.route[idx].wprta_window_size[wpidx] = tw_size
            else:
                return False, name + 'not found in route' + traf.id[idx]

    def rtwa_at(self, idx, *args):  #
        if len(args) != 3:
            return False, 'RTWA_AT needs 4 arguments'
        else:
            name = args[0]
            tw_size = args[1]
            rta_time = args[2]
            if name in traf.ap.route[idx].wpname:
                wpidx = traf.ap.route[idx].wpname.index(name)
                traf.ap.route[idx].wprta[wpidx] = datetime.strptime(rta_time, '%H:%M:%S').time()
                traf.ap.route[idx].wprta_window_size[wpidx] = tw_size
            else:
                return False, name + 'not found in route' + traf.id[idx]

    def own_from(self, idx, *args):  #
        if len(args) == 1:
            name = args[0]
            if name in traf.ap.route[idx].wpname:
                wpidx = traf.ap.route[idx].wpname.index(name)
                traf.ap.route[idx].wpown[wpidx] = traf.perf.macr[idx]
            else:
                return False, name + 'not found in route' + traf.id[idx]
        elif len(args) == 2:
            name = args[0]
            own_spd_index = args[1]
            if name in traf.ap.route[idx].wpname:
                wpidx = traf.ap.route[idx].wpname.index(name)
                traf.ap.route[idx].wpown[wpidx] = own_spd_index
            else:
                return False, name + 'not found in route' + traf.id[idx]
        else:
            return False, 'OWN_SPD_FROM needs 2 or 3 arguments'

    def _time_s2rta(self, time2):  #
        """
        Calculate time in seconds to next RTA waypoint
        :param time2: RTA in time format
        :return: time in seconds
        """
        time1 = sim.utc.time()
        time_format = '%H:%M:%S'
        tdelta = datetime.strptime(time2.strftime(time_format), time_format) -\
                 datetime.strptime(time1.strftime(time_format), time_format)
        if tdelta.days < 0:
            tdelta = timedelta(days=0, seconds=tdelta.seconds, microseconds=tdelta.microseconds)
            return tdelta.seconds - 86400
        else:
            return tdelta.seconds

    def _cas2rta(self, distances_nm, flightlevels_m, time2rta_s, current_cas_m_s):  #
        """
        Calculate the CAS needed to arrive at the RTA waypoint in the specified time
        No wind is taken into account.
        :param distances: distances between waypoints
        :param flightlevels: flightlevels for sections
        :param time2rta_s: time in seconds to RTA waypoint
        :param current_cas_m_s: current CAS in m/s
        :return: CAS in m/s
        """
        iterations = 4
        estimated_cas_m_s = current_cas_m_s
        for i in range(iterations):
            estimated_time2rta_s = self._eta2tw_new_cas_wfl(distances_nm, flightlevels_m, current_cas_m_s,
                                                            estimated_cas_m_s)
            previous_estimate_m_s = estimated_cas_m_s
            estimated_cas_m_s = estimated_cas_m_s * estimated_time2rta_s / (time2rta_s + 0.00001)
            if abs(previous_estimate_m_s - estimated_cas_m_s) < 0.1:
                break
        return estimated_cas_m_s

    def _eta2tw_cas_wfl(self, distances_nm, flightlevels_m, cas_m_s):
        """
        Estimate for the given CAS the ETA to the next TW waypoint
        No wind is taken into account.
        :param distances: distances between waypoints
        :param flightlevels: flightlevels for sections
        :param cas_m_s: CAS in m/s
        :return: ETA in seconds
        """
        distances_m = distances_nm * 1852
        times_s = np.empty_like(distances_m)
        previous_fl_m = flightlevels_m[0]

        # Assumption is instantanuous climb to next flight level, and instantanuous speed change at new flight level
        for i, distance_m in enumerate(distances_m):
            if flightlevels_m[i] < 0:
                next_fl_m = previous_fl_m
            else:
                next_fl_m = flightlevels_m[i]
                previous_fl_m = next_fl_m
            next_tas_m_s = tools.aero.vcas2tas(cas_m_s, next_fl_m)
            step_time_s = distance_m / (next_tas_m_s + 0.00001)
            times_s[i] = step_time_s

        total_time_s = np.sum(times_s)
        return total_time_s

    def _dtime2new_cas(self, flightlevel_m, current_cas_m_s, new_cas_m_s):
        """
        Estimated delta time between flying at current CAS, and
        changing speed to the new CAS
        :param flightlevel_m: Flightlevel in m
        :param current_cas_m_s: current CAS in m/s
        :param new_cas_m_s: new CAS in m/s
        :return: delta time in seconds
        """
        # acceleration_m_s2 = 0.5
        # deceleration_m_s2 = -0.5

        current_tas_m_s = tools.aero.vcas2tas(current_cas_m_s, flightlevel_m)
        new_tas_m_s = tools.aero.vcas2tas(new_cas_m_s, flightlevel_m)

        if new_tas_m_s > current_tas_m_s:
            #Acceleration
            a = acceleration_m_s2
        else:
            #Deceleration
            a = deceleration_m_s2
        return 0.5 * (new_tas_m_s - current_tas_m_s) ** 2 / a / (current_tas_m_s + 0.000001)

    def _eta2tw_new_cas_wfl(self, distances_nm, flightlevels_m, current_cas_m_s, new_cas_m_s):  #
        """
        Estimate for the current CAS and new CAS the ETA to the next TW waypoint
        No wind is taken into account.
        :param distances: distances between waypoints
        :param flightlevels: flightlevels for sections
        :param current_cas_m_s: current CAS in m/s
        :param new_cas_m_s: new CAS in m/s
        :return: ETA in seconds
        """
        total_time_s = self._eta2tw_cas_wfl(distances_nm, flightlevels_m, new_cas_m_s) - \
                       self._dtime2new_cas(flightlevels_m[0], current_cas_m_s, new_cas_m_s)
        if total_time_s < 0:
            return 0.0
        else:
            return total_time_s