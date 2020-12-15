import numpy as np
from datatime import datatime, timedelta

EARTH_D = 6371
MAX_E = 10
VOT = 0.1
SPEED = 30
LOSS_COST = 5

class parking_block():
    def __init__(self, params, dist):
        # params are from csv file
        # dist records the distance between each two blocks

        self.block_id = params['BLOCKFACE_ID']
        self.loc = (params['LONGITUDE'], params['LATITUDE'])
        self.capacity = params['SPACE_NUM']
        self.occupied = 0   # the count of occupied meters
        self.dist = np.sort(dist)
        self.backup_block = np.argsort(dist)    # the priority of back-up blocks

    def is_full(self):
        return self.capacity == self.occupied

    def inc_v(self):
        self.occupied += 1

    def dec_v(self):
        self.occupied -= 1

    def reset(self):
        self.occupied = 0

    def __str__(self):
        return 'Block {id} {loc}\nOccupancy: {occ}/{cap}'.format(
                    id=self.block_id, loc=self.loc,
                    occ=self.occupied, cap=self.capacity)

class vehicle():
    def __init__(self, params):
        self.loc_arrive = params['id']
        self.ind_loc_current = 0
        self.cruising_dist = 0
        self.parked = False
        self.fee = 0
        self.remaining_time = 0

    def dec_time(self):
        if self.remaining_time > 0:
            self.remaining_time -= 1

    def inc_ind_loc(self):
        self.ind_loc_current += 1

    def __str__(self):
        return 'Vehicle arrived at Block {arr}\n{parked}parked{text}'.format(
                    arr=self.loc_arrive, parked='' if self.parked else 'not',
                    text=' at the {cur}-th nearest block with total cruising distance {dist}\nThe remaining parking time is {rt}.'.format(
                        cur=self.ind_loc_current, dist=self.cruising_dist, rt=self.remaining_time) if self.parked else '')

class parking_env():
    def __init__(self, df_block, df_demand):
        self.date = datatime(2019,12,1)
        self.t = 0
        mat_distance = self.great_circle_v(df_block['LONGITUDE'].values, df_block['LATITUDE'].values)
        self.blocks = [parking_block(record, mat_distance[i]) for i, record in enumerate(df_block.to_dict('records'))]
        self.vehicles = np.empty(0)
        self.ob_dim = 2 + len(self.blocks)
        self.ac_dim = len(self.blocks)

    def seed(self, s):
        np.random.seed(s)

    def identify_stage(dt):
        if dt < datetime(2020, 3, 15): # before
            return 0
        elif (dt >= datetime(2020, 3, 15)) & (dt < datetime(2020, 5, 17)): # shutdown
            return 1
        elif (dt >= datetime(2020, 5, 17)) & (dt < datetime(2020, 7, 17)): # reopen
            return 2
        elif (dt >= datetime(2020, 7, 17)) & (dt < datetime(2020, 9, 30)): # closed_due_to_state_re
            return 3
        elif (dt >= datetime(2020, 9, 30)) & (dt < datetime(2020, 10, 20)): # orange
            return 4
        elif (dt >= datetime(2020, 10, 20)) & (dt < datetime(2020, 11, 13)): # yellow
            return 5
        elif dt >= datetime(2020, 11, 13): # rollback
            return 6

    # calculate the great circle distance for all the blocks with matrix form
    def great_circle_v(self, lon, lat):
        lon, lat = np.radians(lon), np.radians(lat)
        return EARTH_D * np.arccos(np.sin(lat) * np.sin(lat).reshape(-1, 1) \
                + np.cos(lat) * np.cos(lat).reshape(-1, 1) * np.cos(lon-lon.reshape(-1, 1)))

    # generate demand for each block at time t
    def generate_demand(self):
        return np.ones(len(self.blocks)).astype(int)

    def simulate_v_park(self, v, p):
        ind_cur_block = self.blocks[v.loc_arrive].backup_block[v.ind_loc_current]
        if not self.blocks[ind_cur_block].is_full():
            if np.random.rand() > 0.1:
                v.parked = True
                self.blocks[ind_cur_block].inc_v()
                v.remaining_time = 2
                v.fee = v.remaining_time * p[ind_cur_block]
            else:
                v.inc_ind_loc()
                new_ind_block = self.blocks[v.loc_arrive].backup_block[v.ind_loc_current]
                v.cruising_dist = self.blocks[ind_cur_block].dist[new_ind_block]

    # simulate the parking behavior with choice model
    def do_simulation(self, a):
        self.date = self.date + timedelta(minutes=30)
        self.t = self.date.hour*2
        self.stage = self.identify_stage(self.date)

        # parked vehicles
        ind_vehicles = []
        for i, v in enumerate(self.vehicles):
            v.dec_time()
            if v.remaining_time == 0:
                ind_cur_block = self.blocks[v.loc_arrive].backup_block[v.ind_loc_current]
                self.blocks[ind_cur_block].dec_v()
            else:
                ind_vehicles.append(i)
        self.vehicles = self.vehicles[ind_vehicles]

        # parking vehicles
        num_parked_vehicles = len(self.vehicles)
        d = self.generate_demand()
        for i, block in enumerate(self.blocks):
            self.vehicles = np.append(self.vehicles, [vehicle({'id': i}) for _ in range(d[i])])
        for t_e in range(MAX_E-1):
            for v in self.vehicles[num_parked_vehicles:]:
                if not v.parked:
                    self.simulate_v_park(v, a)

        reward = 0
        for v in self.vehicles[num_parked_vehicles:]:
            if v.parked:
                reward += v.fee - v.cruising_dist / SPEED * VOT
            else:
                reward -= LOSS_COST

        return reward

    # given the action, simulate the process and get the reward
    def step(self, a):
        reward = self.do_simulation(a)
        ob = self._get_obs()
        done = self.date >= datatime(2020, 11, 30)
        return ob, reward, done, None

    def _get_obs(self):
        return np.concatenate([[self.stage, self.t], [block.occupied for block in self.blocks]])

    def reset_model(self):
        self.t = 0
        self.vehicles = np.empty(0)
        for b in self.blocks:
            b.reset()
        return self._get_obs()

    def reset(self):
        ob = self.reset_model()
        return ob

    def __str__(self):
        return 'At time {t}, there are {num_b} blocks and {num_v} vehicles.'.format(
                    t=self.t, num_b=len(self.blocks), num_v=len(self.vehicles))