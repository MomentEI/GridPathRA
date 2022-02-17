# -*- coding: utf-8 -*-
"""
Created on Sat Jun 26 14:51:11 2021

@author: elain
"""

import threading
import multiprocessing
import numpy as np
import csv
import os
import datetime
import sys
import glob
import shutil
from psutil import Process
import gc

class Parameter:
    def __init__(self,name,aggregation,vartype,scalar):
        self.name = name
        self.aggregation = aggregation
        self.vartype = vartype
        self.scalar = scalar
        
        self.timeseries = []
        self.unitModel = 'NA'
        self.unitFOR = 0
        self.unitMTTR = 1
        self.unitMTTF = 1
        self.units = 1
        self.gentieModel = 'NA'
        self.gentieFOR = 0
        self.gentieMTTR = 1
        self.gentieMTTF = 1

class Aggregation:
    def __init__(self,name,temporal):
        self.name = name
        self.temporal = temporal  
        
        self.total = 0

class VarType:
    def __init__(self,name,filename,print_mode,header,value):
        self.name = name
        self.filename = filename
        self.print_mode = print_mode
        self.header = header  
        self.value = value

class Timeseries:
    def __init__(self,name,stat):
        self.name = name
        self.stat = stat
        
        self.draw_inds = []
        self.rows = 0


def timeseries_sync(case_name,timeseries,timeseries_inds1):
    
    N_timeseries = len(timeseries[0])
    
    # Pull sycnhronized weather days based on what's available in the historical record
    print('identifying synchronized conditions...')

    timeseries_header = []
    timeseries_timestamps = []
    timeseries_timestamps_dt64 = []
    weather_timestamps_dt64 = []
    hydro_timestamps_dt64 = []
    for i in range(N_timeseries): 
        
        ts = timeseries[1][i]
        print(''.join(('  ...',ts.name,'...')))
        
        # pull in all the timestamps
        with open(os.path.join('historical_data',ts.name,'timestamps.csv')) as csvfile:
            file_reader = csv.reader(csvfile, delimiter=',')
            header = file_reader.__next__()
            
            if 'HE' in header:
                timeseries_header.append(header[0:-1])
            else:
                timeseries_header.append(header)
            timeseries_timestamps.append([])
            timeseries_timestamps_dt64.append([])
            ts.rows = 0
            for row in file_reader:
                ts.rows += 1
                # store the timestamp so that it can be used later to report out the drawn days
                if 'HE' in header:
                    timeseries_timestamps[i].append(row[0:-1])
                    HE = row[header.index('HE')].zfill(2)
                    if HE == '01':
                        year = row[header.index('year')]
                        month = row[header.index('month')].zfill(2)
                        # if daily data is not provided, assign the row to the first day of the month
                        if 'day' not in header:
                            day = '01'
                        else:
                            day = row[header.index('day')].zfill(2)
                        timestamp_tmp = np.datetime64(''.join((year,'-',month,'-',day)))
                        if timestamp_tmp not in timeseries_timestamps_dt64[i]:
                            timeseries_timestamps_dt64[i].append(timestamp_tmp)
                else:
                    timeseries_timestamps[i].append(row)
                
                    year = row[header.index('year')]
                    month = row[header.index('month')].zfill(2)
                    # if daily data is not provided, assign the row to the first day of the month
                    if 'day' not in header:
                        day = '01'
                    else:
                        day = row[header.index('day')].zfill(2)
                    timestamp_tmp = np.datetime64(''.join((year,'-',month,'-',day)))
                    if timestamp_tmp not in timeseries_timestamps_dt64[i]:
                        timeseries_timestamps_dt64[i].append(timestamp_tmp)
        
        # find the unique timestamps that are common to all weather-based timeseries
        if ts.stat == 'met' or ts.stat == 'cmb':
            if len(weather_timestamps_dt64) == 0:
                weather_timestamps_dt64 = timeseries_timestamps_dt64[i]
            else:
                weather_timestamps_dt64 = np.intersect1d(weather_timestamps_dt64,timeseries_timestamps_dt64[i])
        # find the unique timestamps that are common to all hydro-based timeseries
        elif ts.stat == 'hyd':
            if len(hydro_timestamps_dt64) == 0:
                hydro_timestamps_dt64 = timeseries_timestamps_dt64[i]
            else:
                hydro_timestamps_dt64 = np.intersect1d(hydro_timestamps_dt64,timeseries_timestamps_dt64[i])
        else:
            print(''.join(('Error - bin data for statistical model ', ts.stat ,' not found.')))
    
    
    # convert unique timestamps to array format
    hydro_timestamps = np.zeros([len(hydro_timestamps_dt64),2],dtype=int)
    for i in range(len(hydro_timestamps_dt64)):
        dt64_tmp = hydro_timestamps_dt64[i]
        hydro_timestamps[i,:] = [dt64_tmp.astype('object').year,dt64_tmp.astype('object').month]
    weather_timestamps = np.zeros([len(weather_timestamps_dt64),3],dtype=int)
    for i in range(len(weather_timestamps_dt64)):
        dt64_tmp = weather_timestamps_dt64[i]
        weather_timestamps[i,:] = [dt64_tmp.astype('object').year,dt64_tmp.astype('object').month,dt64_tmp.astype('object').day]
    
    hydro_years = np.unique(hydro_timestamps[:,0])
    if len(hydro_years) == 0:
        hydro_years = [0]
    weather_years = np.unique(weather_timestamps[:,0])
    if len(weather_years) == 0:
        weather_years = [0]
    sim_years = len(hydro_years)*len(weather_years)

    # initialize file to store draw data for each time series
    draw_data_file = open(os.path.join(''.join((case_name,'_log')),'draw_data.csv'),'w',newline='')
    draw_data_writer = csv.writer(draw_data_file)
    header = ['horizon','day','hydro year','month','weather year','weekend']
    for i in range(N_timeseries):
        ts = timeseries[1][i]
        for h in timeseries_header[i]:
            header.append(''.join((ts.name,' ',h)))
    draw_data_writer.writerow(header)
    
    # Simulate weather days over 52 weeks of each weather year for each hydro year
    print('combining hydro and weather conditions over synchronous records...')
    
    T = 0
    timepoint = []
    tp_horizon = []
    month_print = []
    HE_print = []
    draw_digits = len(str(sim_years*52))
    # loop through hydro years
    for i in range(len(hydro_years)):
        
        print(''.join(('  hydro year: ',str(hydro_years[i]))))
        # loop through the weather days, capturing 52 weeks each year
        year_last = int(weather_timestamps[0,0])
        day_of_year = 1
        
        print(''.join(('    weather year: ',str(year_last))))
        for j in range(np.shape(weather_timestamps)[0]):
            
            # determine the weather year
            weather_yr = int(weather_timestamps[j,0])
            
            # if the weather year has changed from the prior timestamp, reset the day_of_year to 1
            if weather_yr != year_last:
                print(''.join(('    weather year: ',str(weather_yr))))
                day_of_year = 1
                year_last = weather_yr
            
            # only record the day if it's in the first 52 weeks of the year
            if day_of_year <= 52*7:
                # determine the month
                mo_tmp = int(weather_timestamps[j,1])-1
                
                # pull the corresponding hydro index
                hydro_ind = i*12 + mo_tmp
                
                # prepare draw information to print
                horizon_print = np.floor(T/(24*7))+1
                day_print = np.floor(T/24)+1
                draw_data_tmp = np.array([int(horizon_print),int(day_print),hydro_years[i],mo_tmp+1,weather_yr,9999])
                
                # loop through the timeseries
                for k in range(N_timeseries): 
            
                    ts = timeseries[1][k]
                    if ts.stat == 'met' or ts.stat == 'cmb':
                        draw_tmp = int(np.where(timeseries_timestamps_dt64[k] == weather_timestamps_dt64[j])[0]*24)
                    elif ts.stat == 'hyd':
                        draw_tmp = int(hydro_ind)
                    else:
                        print(''.join(('Error - bin data for statistical model ', ts.stat ,' not found.')))
                    ts.draw_inds.append(draw_tmp)
                        
                    # record day in draw data
                    draw_data_tmp = np.append(draw_data_tmp,np.array(timeseries_timestamps[k][draw_tmp]))
                        
                # print day data    
                draw_data_writer.writerow(draw_data_tmp)
                
                # record the hourly timepoints
                week_of_year = np.ceil(day_of_year/7)
                day_of_week = day_of_year - (week_of_year-1)*7
                for hr in range(24):
                    #hr_of_week = int((day_of_week-1)*24+hr+1)
                    timepoint.append(str(int(horizon_print)).zfill(draw_digits)+str(int((day_of_week-1)*24+hr+1)).zfill(3))
                    tp_horizon.append(int(horizon_print))
                    month_print.append(mo_tmp+1)
                    HE_print.append(hr+1)
               
                T += 24
                
            # go to the next day
            day_of_year += 1

    draw_data_file.close()


    return timeseries, timepoint, tp_horizon, month_print, HE_print
    
def timeseries_MC(case_name,timeseries,timeseries_inds1,iterations):
    
    N_timeseries = len(timeseries[0])
    
    # Import bin data
    print('importing weather bins...')
    weatherbin_timestamp = []
    weatherbin_month = []
    weatherbin_weekend = []
    weatherbin_weather = []
    with open('bins/weather_bins.csv') as csvfile:
        file_reader = csv.reader(csvfile, delimiter=',')
        file_reader.__next__()
        for row in file_reader:
            year = row[0]
            month = row[1].zfill(2)
            day = row[2].zfill(2)
            weatherbin_timestamp.append(np.datetime64(''.join((year,'-',month,'-',day))))
            weatherbin_month.append(int(row[3]))
            weatherbin_weekend.append(int(row[4]))
            weatherbin_weather.append(int(row[5]))
    weatherbin_month = np.array(weatherbin_month)
    weatherbin_weekend = np.array(weatherbin_weekend)
    weatherbin_weather = np.array(weatherbin_weather)
    # create an array that represents the weather on the prior day
    weatherbin_priorweather = np.ones(np.shape(weatherbin_weather))
    weatherbin_priorweather[1:-1] = weatherbin_weather[0:-2]
    
    print('importing hydro bins...')
    hydrobin_timestamp = []
    hydrobin_month = []
    hydrobin_hydro = []
    with open('bins/hydro_bins.csv') as csvfile:
        file_reader = csv.reader(csvfile, delimiter=',')
        file_reader.__next__()
        for row in file_reader:
            year = row[0]
            month = row[1].zfill(2)
            hydrobin_timestamp.append(np.datetime64(''.join((year,'-',month,'-01'))))
            hydrobin_month.append(int(row[2]))
            hydrobin_hydro.append(int(row[3]))
    hydrobin_month = np.array(hydrobin_month)
    hydrobin_hydro = np.array(hydrobin_hydro)
    
    
    # map the historical timeseries data to the bins
    print('binning historical timeseries...')
    timeseries_header = []
    timeseries_timestamps = []
    for i in range(N_timeseries):
        
        ts = timeseries[1][i]
        
        print(''.join(('  ...',ts.name,'...')))
        
        # pull timestamps from associated bin data
        if ts.stat == 'met' or ts.stat == 'cmb':
            bin_timestamp = weatherbin_timestamp
        elif ts.stat == 'hyd':
            bin_timestamp = hydrobin_timestamp
        else:
            print(''.join(('Error - bin data for statistical model ', ts.stat ,' not found.')))
        
        # note - this code only pulls timestamps that have bin assignments
        timeseries_inds1[i] = np.zeros(np.shape(bin_timestamp))
        ts.rows = 0
        with open(os.path.join('historical_data',ts.name,'timestamps.csv')) as csvfile:
            file_reader = csv.reader(csvfile, delimiter=',')
            header = file_reader.__next__()
            if 'HE' in header:
                timeseries_header.append(header[0:-1])
            else:
                timeseries_header.append(header)
            timeseries_timestamps.append([])
            t = 1
            for row in file_reader:
                ts.rows += 1
                # store the timestamp so that it can be used later to report out the drawn days
                if 'HE' in header:
                    timeseries_timestamps[i].append(row[0:-1])
                else:
                    timeseries_timestamps[i].append(row)
                
                year = row[header.index('year')]
                month = row[header.index('month')].zfill(2)
                # if daily data is not provided, assign the row to the first day of the month
                if 'day' not in header:
                    day = '01'
                else:
                    day = row[header.index('day')].zfill(2)
                timestamp_tmp = np.datetime64(''.join((year,'-',month,'-',day)))
                if ('HE' in header and int(row[header.index('HE')]) == 1) or 'HE' not in header:
                    timeseries_inds1[i][bin_timestamp == timestamp_tmp] = t
    
                # update the index tracker - t represents the index (base 1) of the historical data corresponding to each binned day
                t += 1

    # initialize file to store draw data for each time series
    draw_data_file = open(os.path.join(''.join((case_name,'_log')),'draw_data.csv'),'w',newline='')
    draw_data_writer = csv.writer(draw_data_file)
    header = ['horizon','day','hydro year','month','weather bin','weekend']
    for i in range(N_timeseries):
        ts = timeseries[1][i]
        for h in timeseries_header[i]:
            header.append(''.join((ts.name,' ',h)))
    draw_data_writer.writerow(header)
    
    
    # Simulate weather days over 52 weeks for each simulation year
    print('randomly drawing conditions...')
    np.random.seed(seed=0)
    T = 0
    timepoint = []
    tp_horizon = []
    month_print = []
    HE_print = []
    N_digits = len(str(iterations))
    
    for yr in range(iterations):
        
        if np.mod(yr+1,10) == 0:
            print(''.join(('  ...year ',str(yr+1),' of ',str(iterations),'..')))
        
        # randomly draw hydro conditions - check this logic to see if it can pick 0 and max indices
        hydro_tmp = hydrobin_hydro[np.random.randint(len(hydrobin_hydro))]
        
        # start with the first calendar day of the study year
        day_tmp = np.datetime64(''.join((str(study_year),'-01-01')))
        
        # randomly draw the weather conditions on the last day of the prior year
        weatherbin_weather_sub = weatherbin_weather[weatherbin_month == 12]
        prior_weather = weatherbin_weather_sub[np.random.randint(len(weatherbin_weather_sub))]
        
        n_days = 1
        while day_tmp.astype(object).year == study_year and n_days <= 52*7:
            
            # determine the month and whether the day is a weekend or weekday
            mo_tmp = day_tmp.astype(object).month
            weekend_tmp = (day_tmp.astype(datetime.datetime).isoweekday() > 5)*1
            
            # randomly pick the weather bin from the days within the month where the prior day matched the prior weather bin
            weatherbin_weather_sub = weatherbin_weather[(weatherbin_priorweather == prior_weather)*(weatherbin_month == mo_tmp)]
            weather_tmp = weatherbin_weather_sub[np.random.randint(len(weatherbin_weather_sub))]
            
            # find all the days (hourly data) or months (monthly data) in the selected bins
            met_inds_tmp = (weatherbin_month == mo_tmp)*(weatherbin_weather == weather_tmp)
            cmb_inds_tmp = met_inds_tmp*(weatherbin_weekend == weekend_tmp)
            hyd_inds_tmp = (hydrobin_month == mo_tmp)*(hydrobin_hydro == hydro_tmp)
            
            # prepare draw information to print
            horizon_print = np.floor(T/(24*7))+1
            day_print = np.floor(T/24)+1
            draw_data_tmp = np.array([horizon_print,day_print,hydro_tmp,mo_tmp,weather_tmp,weekend_tmp])
            
            # loop through the timeseries
            for i in range(N_timeseries):
                ts = timeseries[1][i]
                # find the corresponding historical days based on the statistical model
                inds_tmp = eval(ts.stat+'_inds_tmp')
                # find the overlap with the available days of timeseries data
                timeseries_inds_tmp = timeseries_inds1[i][inds_tmp*(timeseries_inds1[i] > 0)]
                # randomly draw and record a day from the overlapping available timeseries data
                draw_tmp = int(timeseries_inds_tmp[np.random.randint(len(timeseries_inds_tmp))] - 1)
                ts.draw_inds.append(draw_tmp)
                # record drawn day in draw data
                draw_data_tmp = np.append(draw_data_tmp,np.array(timeseries_timestamps[i][draw_tmp]))
            
            # print draw data    
            draw_data_writer.writerow(draw_data_tmp)
            
            # record the hourly timepoints
            week_of_year = int(np.ceil(n_days/7))
            day_of_week = n_days - (week_of_year-1)*7
            for hr in range(24):
                hr_of_week = int((day_of_week-1)*24+hr+1)
                timepoint.append(str(yr+1).zfill(N_digits)+str(week_of_year).zfill(2)+str(hr_of_week).zfill(3))
                tp_horizon.append(int(horizon_print))
                month_print.append(mo_tmp)
                HE_print.append(hr+1)
            
            # go to the next day
            n_days += 1
            day_tmp += np.timedelta64(1,'D')
            prior_weather = weather_tmp
            T += 24
    
    draw_data_file.close()
    
    return timeseries, timepoint, tp_horizon, month_print, HE_print



def simulate_aggregation(a,case_name,study_year,timepoint_sub,parameters,vartypes,timeseries,weather_mode,iterations,print_flag,output_mode):

    sys.stdout = open(os.path.join(''.join((case_name,'_log')),''.join((a.name,'.out'))), 'w')
    sys.stderr = open(os.path.join(''.join((case_name,'_log')),''.join((a.name,'.err'))), 'w')
    
    # determine the number of horizons simulated for each forced outage iteration
    N_horizons = int(len(timepoint_sub)/168)
    
    # if running synchronized weather, set it up to loop through the forced outage iterations. Otherwise the timeseries data already loops through iterations, so no need to iterate again here
    if weather_mode == 'Synchronized':
        FO_iterations = iterations
    elif weather_mode == 'MonteCarlo':
        FO_iterations = 1
    else:
        print('Error - Weather mode not recognized')
        sys.stdout.flush()

    # pull the aggreation object
    #a = aggregations[1][agg_ind]

    # determine the number of rows of data for each draw
    if a.temporal == 'timepoint':
        N = 168
    elif a.temporal == 'horizon':
        N = 1
    else:
        print('Error - temporal structure not recognized')
        sys.stdout.flush()
        
    # determine the time between each draw (for the failure and repair model)
    dt = 168/N
    
    # loop through variable types
    for v in range(len(vartypes[0])):
        
        # only generate outputs if there is non-zero capacity associated with the variable type for the aggregation
        if a.total[v] > 0:
            
            print(''.join((a.name,' - ',vartypes[0][v])))
            sys.stdout.flush()
            
            print(''.join(('  Memory usage before running: ',str(int(Process().memory_info().rss/1000000)),' MB')))
            sys.stdout.flush()
            
            # determine number of columns of data
            M = len(vartypes[1][v].value)
            
            # determine the rounding precision for the aggregation
            N_round = len(str(int(a.total[v]))) + 1
            
            # if in database mode, initialize file for printing aggregation data
            stage_id = '1'
            if output_mode == 'db':
                if vartypes[1][v].print_mode == 'new file':
                    filename_tmp = os.path.join(case_name,vartypes[1][v].filename,''.join((a.name,'-1-',case_name,'.csv')))
                elif vartypes[1][v].print_mode == 'append file':
                    filename_tmp = os.path.join(case_name,vartypes[1][v].filename,'tmp',''.join((a.name,'.csv')))
                else:
                    print('Error - print mode not recognized')
                    sys.stdout.flush()
                db_file = open(filename_tmp,'a',newline='')
                db_writer = csv.writer(db_file)
                db_writer.writerow(vartypes[1][v].header)
            
            # pull in historical timeseries data for all parameters in the aggregation
            param_historical_data = []
            N_params = 0
            agg_params = []
            for i in range(len(parameters[0])):
                
                # pull the parameter
                p = parameters[1][i]
                
                # only proceed if the parameter corresponds to the aggregation and variable type
                if p.aggregation == a.name and p.vartype == vartypes[0][v]:
                    
                    # store the parameter index
                    agg_params.append(i)
                    
                    # pull in any parameter timeseries data 
                    if p.timeseries != []:
                        ts_ind = timeseries[0].index(p.timeseries)
                        param_historical_data.append(np.zeros([timeseries[1][ts_ind].rows,M]))
                        with open(os.path.join('historical_data',p.timeseries,''.join((p.name,'.csv')))) as csvfile:
                            file_reader = csv.reader(csvfile)
                            ind_tmp = 0
                            for row in file_reader:
                                if ind_tmp == 0:
                                    M_hist = len(row)
                                param_historical_data[N_params][ind_tmp,0:M_hist] = np.array(row,dtype=float)
                                ind_tmp += 1
                            param_historical_data[N_params] = param_historical_data[N_params][:,0:M_hist]
                    else:
                        param_historical_data.append(np.zeros(1))
                    
                    # count the number of parameters in the iteration 
                    N_params += 1
            
            print(''.join(('  Memory usage after loading historical data: ',str(int(Process().memory_info().rss/1000000)),' MB')))
            sys.stdout.flush()
            
            print(''.join(('  Parameters: ',str(N_params))))
            sys.stdout.flush()
            
            # loop through the horizons
            for n in range(N_horizons):
                
                
                # if the print flag is on for the horizon
                if print_flag[n] == 1:
   
                    # initialize an array to store the total aggregation availability
                    data = np.zeros([N*FO_iterations,M])
                    
                    # loop through the parameters in the aggregation
                    for i in range(N_params):
                        
                        # pull the parameter
                        p = parameters[1][agg_params[i]]
        
                        # pull the timeseries data corresponding to the draws
                        if p.timeseries != []:
                            
                            # initialize an array to store the timeseries data associated with the parameter draw
                            param_draw_ts = np.zeros([N,M])
                            
                            # pull the parameter timeseries indices associated with all horizons
                            day_inds_tmp = timeseries[1][ts_ind].draw_inds
                            
                            # determine the number of columns of historical data
                            M_hist = np.shape(param_historical_data[i])[1]
                            
                            # loop through the days in the horizon
                            for d in range(7):
                                if a.temporal == 'timepoint':
                                    # downscale from day to hours
                                    param_draw_ts[d*24:(d+1)*24,0:M_hist] = param_historical_data[i][day_inds_tmp[n*7+d]:day_inds_tmp[n*7+d]+24,:]
                                elif a.temporal == 'horizon':
                                    # upscale from day to week
                                    param_draw_ts[0,0:M_hist] += param_historical_data[i][day_inds_tmp[n*7+d],:]/7
                                else:
                                    print('Error - temporal structure not recognized')
                                    sys.stdout.flush()
                        else:
                            # if no timeseries data is available for the parameter, initialize the parameter availability with ones
                            param_draw_ts = np.ones([N,M])
                    
                        # initialize an array to store the parameter availability across the forced outage iterations
                        param_draw_data = np.zeros([N*FO_iterations,M])
                        # simulate forced outages
                        for k in range(FO_iterations):
                            
                            # set the availability in the draw equal to the availability based on timeseries data
                            param_draw_data[k*N:(k+1)*N] = param_draw_ts
                               
                            # simulate unit forced outages
                            if p.unitModel != 'NA':
                                
                                if p.unitModel == 'Derate' or (weather_mode == 'Synchronized' and k == 0):
                                    # use a flat forced outage derate to scale the parameter availability
                                    param_draw_data[k*N:(k+1)*N] *= (1-p.unitFOR)
                                    
                                elif p.unitModel == 'MonteCarlo':
                                    if p.unitFOR > 0:
                                        
                                        # randomly draw the starting state in the first time step for each unit
                                        avail_tmp = 1.0-(np.random.rand(p.units) < p.unitFOR)
                                        
                                        # loop through the timesteps in the draw
                                        for h in range(N):
                                            # calculate the availability of each unit using an exponential failure and repair model
                                            avail_tmp = (avail_tmp == 1)*(1.0 - (np.random.exponential(p.unitMTTF,p.units) < dt)) + (avail_tmp == 0)*(np.random.exponential(p.unitMTTR,p.units) < dt)
                                            # use the average availability across the units to scale the parameter availability
                                            param_draw_data[k*N+h] *= np.mean(avail_tmp)

                                else:
                                    print('Error - Unit forced outage model not recognized.')
                                    sys.stdout.flush()
                            
                            # simulate gen tie forced outages
                            if p.gentieModel != 'NA':
                                
                                if p.gentieModel == 'Derate' or (weather_mode == 'Synchronized' and k == 0):
                                    # use a flat forced outage derate to scale the parameter availability
                                    param_draw_data[k*N:(k+1)*N] *= (1-p.gentieFOR)
                                
                                elif p.gentieModel == 'MonteCarlo':
                                    if p.gentieFOR > 0:
                                        
                                        # randomly draw the starting state in the first time step
                                        avail_tmp = 1.0-(np.random.rand() < p.gentieFOR)
                                        
                                        # loop through the timesteps in the draw
                                        for h in range(N):
                                            # calculate the gen tie availability using an exponential failure and repair model
                                            # if it's starting online, determine whether it experiences an outage
                                            if avail_tmp == 1:
                                                avail_tmp = 1.0 - (np.random.exponential(p.gentieMTTF) < dt)
                                            # if it's starting in an outage, determine whether it comes back online
                                            else:
                                                avail_tmp = (np.random.exponential(p.gentieMTTR) < dt)*1.0
                                            # use the resulting gen tie availability to scale the parameter availability
                                            param_draw_data[k*N+h] *= avail_tmp
                                else:
                                    print('Error - Gen tie forced outage model not recognized.')
                                    sys.stdout.flush()
                        
                        # add the weighted parameter availability to the aggregation availability (weighted average across the parameters)
                        data += param_draw_data*p.scalar/a.total[v]
                    
                    # round data to reduce file sizes
                    data = np.round(data,N_round)
                    
                    # pull the array of strings that should be evaluated to print the data associated with the variable type
                    value_tmp = vartypes[1][v].value

                    # loop through the forced outage iterations
                    for k in range(FO_iterations):
                        
                        # determine the draw number (to be printed to file)
                        horizon = int(k*N_horizons) + n + 1
                        
                        if output_mode == 'files':
                            
                            # print to a draw-specific file
                            filename_tmp = os.path.join(case_name,str(horizon),'inputs',''.join((vartypes[1][v].filename,'_tmp')),''.join((a.name,'.csv'))) 
                            with open(filename_tmp,'w',newline='') as csvfile:
                                files_writer = csv.writer(csvfile)
                                files_writer.writerow(vartypes[1][v].header)
                                # loop through timesteps
                                for i in range(N):
        
                                    # determine row of data array corresponding to the forced outage iteration and timestep
                                    t = k*N + i
                                
                                    # determine the corresponding timepoint to print to file
                                    if weather_mode == 'Synchronized':
                                        timepoint = ''.join((str(k+1).zfill(len(str(iterations))),str(timepoint_sub[n*N+i])))
                                    elif weather_mode == 'MonteCarlo':
                                        timepoint = str(timepoint_sub[n*N+i])
                                    else:
                                        print('Error - Weather mode not recognized')
                                        sys.stdout.flush()
                                    
                                    row_tmp = []
                                    for value in value_tmp:
                                        row_tmp.append(eval(value))
                                    files_writer.writerow(row_tmp)
                               
                        elif output_mode == 'db':
                            
                            # print to the database file
                            # loop through timesteps
                            for i in range(N):
        
                                # determine row of data array corresponding to the forced outage iteration and timestep
                                t = k*N + i
                            
                                # determine the corresponding timepoint to print to file
                                if weather_mode == 'Synchronized':
                                    timepoint = ''.join((str(k+1).zfill(len(str(iterations))),str(timepoint_sub[n*N+i])))
                                elif weather_mode == 'MonteCarlo':
                                    timepoint = str(timepoint_sub[n*N+i])
                                else:
                                    print('Error - Weather mode not recognized')
                                    sys.stdout.flush()
                                
                                row_tmp = []
                                for value in value_tmp:
                                    row_tmp.append(eval(value))
                                db_writer.writerow(row_tmp)
                        

                
            # if in database output mode, close the file for the aggregation
            if output_mode == 'db':
                db_file.close()
            
            print(''.join(('  Memory usage after writing draws: ',str(int(Process().memory_info().rss/1000000)),' MB')))
            sys.stdout.flush()
    
    sys.stdout.close()
    sys.stderr.close()

        

def remove_draws(draw_folders):
    
    for draw in draw_folders:
        shutil.rmtree(draw)


def print_temporal_files(case_name,vartypes,draw_list,FO_iterations,timepoint_sub,tp_horizon_sub,months,month_print,weather_mode,study_year):

    N_timepoints = len(timepoint_sub)
    N_horizons = np.max(tp_horizon_sub)
    N_digits = len(str(FO_iterations))
    print_flag = np.zeros(N_horizons)
    
    for j in draw_list:
        
        # determine the horizon number
        i = int(np.mod(j,N_horizons))
        # determine the forced outage iteration number
        k = int((j-i)/N_horizons)
        
        # determine if the current horizon overlaps with the months for the run
        if month_print[i*168] in months or month_end in months:
            
            # create the directories for the draw
            for vartype in vartypes[1]:
                if os.path.exists(os.path.join(case_name,str(j+1),'inputs',''.join((vartype.filename,'_tmp')))) == False:
                    os.makedirs(os.path.join(case_name,str(j+1),'inputs',''.join((vartype.filename,'_tmp'))))
            
            #horizon = j+1
            
            # print periods file
            with open(os.path.join(case_name,str(j+1),'inputs','periods.tab'),'w',newline='') as csvfile:
                csvwriter = csv.writer(csvfile, delimiter = '\t')
                csvwriter.writerow(['period','discount_factor','period_start_year','period_end_year','hours_in_period_timepoints'])
                csvwriter.writerow([study_year,'1',study_year,study_year+1,int(N_timepoints*FO_iterations)])
            
            # print horizons file
            with open(os.path.join(case_name,str(j+1),'inputs','horizons.tab'),'w',newline='') as csvfile:
                csvwriter = csv.writer(csvfile, delimiter = '\t')
                csvwriter.writerow(['horizon','balancing_type_horizon','boundary'])
                csvwriter.writerow([j+1,'week','circular'])

            # initialize horizon_timepoints file
            horizon_timepoints_file = open(os.path.join(case_name,str(j+1),'inputs','horizon_timepoints.tab'),'w',newline='')
            horizon_timepoints_writer = csv.writer(horizon_timepoints_file, delimiter = '\t')
            header = ['horizon','balancing_type_horizon','timepoint']
            horizon_timepoints_writer.writerow(header)
            
            # initialize timepoints file
            timepoint_file = open(os.path.join(case_name,str(j+1),'inputs','timepoints.tab'),'w',newline='')
            timepoint_writer = csv.writer(timepoint_file, delimiter = '\t')
            header = ['timepoint','period','timepoint_weight','number_of_hours_in_timepoint','previous_stage_timepoint_map','month']
            timepoint_writer.writerow(header)
            
            print_flag[i] = 1
            for h in range(168): # HERE
                if weather_mode == 'Synchronized':
                    timepoint = ''.join((str(k+1).zfill(N_digits),str(timepoint_sub[i*168 + h])))
                elif weather_mode == 'MonteCarlo':
                    timepoint = str(timepoint_sub[i*168 + h])
                else:
                    print('Error - weather mode not recognized') 
                horizon_timepoints_writer.writerow([j+1,'week',timepoint])
                timepoint_writer.writerow([timepoint,study_year,'1.0','1','.',month_print[i*168 + h]])
    
            timepoint_file.close()
            horizon_timepoints_file.close()
       
        

def consolidate_files(case_name,vartypes,draw_list):
    
    for j in draw_list:
        
        for k in range(len(vartypes[0])):
        
            filename_tmp = os.path.join(case_name,str(j+1),'inputs',''.join((vartypes[1][k].filename,'.tab')))
            if os.path.exists(filename_tmp) == False:
                with open(filename_tmp,'w',newline='') as appended_file:
                    appended_out = csv.writer(appended_file,delimiter = '\t')
                
                    # loop through the output files
                    i = 0
                    for file in glob.glob(os.path.join(case_name,str(j+1),'inputs',''.join((vartypes[1][k].filename,'_tmp')),'*')):
                        with open(file) as csvfile:
                            csvreader = csv.reader(csvfile)
                            if i > 0:
                                csvreader.__next__()
                            else:
                                appended_out.writerow(csvreader.__next__())
                            for row in csvreader:
                                appended_out.writerow(row)
                        i += 1
                
                # delete temporary files
                if os.path.exists(os.path.join(case_name,str(j+1),'inputs',''.join((vartypes[1][k].filename,'_tmp')))):
                    shutil.rmtree(os.path.join(case_name,str(j+1),'inputs',''.join((vartypes[1][k].filename,'_tmp'))))
    
    
if __name__ == '__main__': 
   
    case_name = sys.argv[1]
    no_jobs = int(sys.argv[2])
    
    
    ###########################################################################
    # Remove old directories
    ###########################################################################
    
    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))
    
    print('removing old directories...')
    
    if os.path.isdir(case_name) == True:
        
        old_draws = glob.glob(os.path.join(case_name,'*'))
        N_old_draws = len(old_draws)
        
        if N_old_draws > 0:
            N_old_batch = int(np.ceil(N_old_draws/no_jobs))
            N_jobs = int(min(N_old_draws/N_old_batch,no_jobs))
            
            
            jobs = []
            for n in range(N_jobs):
             
                draw_folders = old_draws[n*N_old_batch:min(N_old_draws,(n+1)*N_old_batch)]
                p = threading.Thread(target=remove_draws,args=(draw_folders,))
                jobs.append(p)
                p.start()
                
            for job in jobs:
                job.join()
        
        # remove the rest of the directory and its contents
        shutil.rmtree(case_name)
        
        
    if os.path.isdir(''.join((case_name,'_log'))):
        shutil.rmtree(''.join((case_name,'_log')))
    
    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))
    
    ###########################################################################
    # Import settings
    ###########################################################################
    
    print('importing scenario information...')
    
    with open('settings/scenario_settings.csv') as csvfile:
        file_reader = csv.reader(csvfile)
        scenarios = file_reader.__next__()
        if case_name not in scenarios:
            print('Error - scenario not listed in scenario_settings.csv')
        else:
            scenario_ind = scenarios.index(case_name)
            study_year = int(file_reader.__next__()[scenario_ind])
            weather_mode = file_reader.__next__()[scenario_ind]
            month_start = int(file_reader.__next__()[scenario_ind])
            month_stop = int(file_reader.__next__()[scenario_ind])
            months = []
            for mo in range(month_start,month_stop+1):
                months.append(mo)
            iterations = int(file_reader.__next__()[scenario_ind])
            parameters_file = file_reader.__next__()[scenario_ind]
            aggregations_file = file_reader.__next__()[scenario_ind]
            timeseries_file = file_reader.__next__()[scenario_ind]
            output_mode = file_reader.__next__()[scenario_ind]
            vartypes_file = ''.join(('variable_types_',output_mode,'.csv'))
            common_files = file_reader.__next__()[scenario_ind]

    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))

    # import variable type settings
    print('importing variable type settings...')
    # vartypes[0] is a list of the vartype names and vartypes[1] is an array of the associated vartype objects
    vartypes = [[],[]]
    if os.path.exists(os.path.join('settings',vartypes_file)):
        with open(os.path.join('settings',vartypes_file)) as csvfile:
            file_reader = csv.reader(csvfile)
            header = file_reader.__next__()
            for row in file_reader:
                vartype_name_tmp = row[0]
                vartypes[0].append(vartype_name_tmp)
                
                vartype_header = []
                vartype_value = []
                for col in range(3,len(row)):
                    if header[col] == 'header' and row[col] != 'NA':
                        vartype_header.append(row[col])
                    if header[col] == 'value' and row[col] != 'NA':
                        vartype_value.append(row[col])
                        
                vartypes[1].append(VarType(vartype_name_tmp,row[1],row[2],vartype_header,vartype_value))
    else:
        print('Error - variable types file not found.')
    
    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))
    
    # import aggregations
    print('importing aggregations...')
    # aggregations[0] is a list of the aggregation names and aggregations[1] is an array of the associated aggregation objects
    aggregations = [[],[]]
    if os.path.exists(os.path.join('settings',aggregations_file)):
        with open(os.path.join('settings',aggregations_file)) as csvfile:
            file_reader = csv.reader(csvfile)
            file_reader.__next__()
            for row in file_reader:
                agg_name_tmp = row[0]
                aggregations[0].append(agg_name_tmp)
                aggregations[1].append(Aggregation(agg_name_tmp,row[1]))
                # initialize vector to store aggregation totals by variable type
                aggregations[1][-1].total = np.zeros(len(vartypes[0]))
    else:
        print('Error - aggregation file not found.')
        
    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))
    
    # import parameters
    print('importing load and resource parameters...')
    # parameters[0] is a list of the parameter names and parameters[1] is an array of the associated parameter objects
    parameters = [[],[]]
    if os.path.exists(os.path.join('settings',parameters_file)):
        with open(os.path.join('settings',parameters_file)) as csvfile:
            file_reader = csv.reader(csvfile)
            file_reader.__next__()
            for row in file_reader:
                # add the parameter to the parameters array
                param_name_tmp = row[0]
                param_agg_tmp = row[1]
                
                # if the parameter aggregation is in the aggregation list, load the parameter
                if param_agg_tmp in aggregations[0]:
                
                    param_var_tmp = row[2]
                    param_scalar_tmp = float(row[3])
                    parameters[0].append(param_name_tmp)
                    parameters[1].append(Parameter(param_name_tmp,param_agg_tmp,param_var_tmp,param_scalar_tmp))
                    
                    # store any timeseries information
                    if row[4] != 'NA':
                        parameters[1][-1].timeseries = row[4]
                    
                    # store any unit forced outage model information
                    param_unitmodel_tmp = row[8]
                    if param_unitmodel_tmp != 'NA':
                        parameters[1][-1].unitModel = param_unitmodel_tmp
                        parameters[1][-1].units = int(row[5])
                        parameters[1][-1].unitFOR = float(row[6])
                        parameters[1][-1].unitMTTR = float(row[7])
                        if parameters[1][-1].unitFOR > 0:
                            parameters[1][-1].unitMTTF = parameters[1][-1].unitMTTR*(1/parameters[1][-1].unitFOR - 1)
                        else:
                            parameters[1][-1].unitMTTF = 0
                    
                    # store any gen tie forced outage model information
                    param_gentiemodel_tmp = row[11]
                    if param_gentiemodel_tmp != 'NA':
                        parameters[1][-1].gentieModel = param_gentiemodel_tmp
                        parameters[1][-1].gentieFOR = float(row[9])
                        parameters[1][-1].gentieMTTR = float(row[10])
                        if parameters[1][-1].gentieFOR > 0:
                            parameters[1][-1].gentieMTTF = parameters[1][-1].gentieMTTR*(1/parameters[1][-1].gentieFOR - 1)
                        else: 
                            parameters[1][-1].gentieMTTF = 0
                            
                    # add the parameter scalar (typically MW) to aggreagtion total
                    aggregations[1][aggregations[0].index(param_agg_tmp)].total[vartypes[0].index(param_var_tmp)] += param_scalar_tmp
                #else:
                    #print(''.join(('  Warning: ',param_agg_tmp,' not in aggregation list, not loading ',param_name_tmp)))
    else:
        print('Error - parameters file not found.')

    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))

    # import timeseries settings
    print('importing timeseries settings...')
    # timeseries[0] is a list of the timeseries names and timeseries[1] is an array of the associated timeseries objects
    timeseries = [[],[]]
    # timeseries_inds1 stores the indices (base 1) of the first time step in each binned period.
    #       - this is an intermediate variable used to determine the indices of periods drawn from the historical data (draw_inds)
    #       - it is not stored as part of the Timeseries object to save space
    timeseries_inds1 = []
    if os.path.exists(os.path.join('settings',timeseries_file)):
        with open(os.path.join('settings',timeseries_file)) as csvfile:
            file_reader = csv.reader(csvfile, delimiter=',')
            file_reader.__next__()
            for row in file_reader:
                timeseries_name_tmp = row[0]
                timeseries[0].append(timeseries_name_tmp)
                timeseries[1].append(Timeseries(timeseries_name_tmp,row[2]))
    
                timeseries_inds1.append([])
    else:
        print('Error - timeseries file not found.')
    
    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))
    
    ###########################################################################
    # Draw conditions and corresponding timeseries indices
    ###########################################################################

    os.mkdir(''.join((case_name,'_log')))
    if weather_mode == 'Synchronized':
        [timeseries, timepoint_sub, tp_horizon_sub, month_print, HE_print] = timeseries_sync(case_name,timeseries,timeseries_inds1)
        N_draws = np.max(tp_horizon_sub)*iterations
    elif weather_mode == 'MonteCarlo':
        [timeseries, timepoint_sub, tp_horizon_sub, month_print, HE_print] = timeseries_MC(case_name,timeseries,timeseries_inds1,iterations)
        N_draws = np.max(tp_horizon_sub)
    else:
        print('Error - weather mode not recognized')
    
    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))    
    
    ###########################################################################
    # Create output directory structure and print temporal files
    ###########################################################################
    
    print('creating output directory structure and printing temporal information...')
    
    # create new directories
    os.mkdir(case_name)
    
    # initialize variable that flags which draws to print to file
    print_flag = np.zeros(N_draws)
    
    if output_mode == 'db':
        os.makedirs(os.path.join(case_name,'temporal'))
        for vartype in vartypes[1]:
            if vartype.print_mode == 'new file':
                os.makedirs(os.path.join(case_name,vartype.filename))
            elif vartype.print_mode == 'append file':
                os.makedirs(os.path.join(case_name,vartype.filename,'tmp'))
            else:
                print('Error - print mode not recognized')
        
        # print period params file
        with open(os.path.join(case_name,'temporal','period_params.csv'),'w',newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerow(['period','discount_factor','period_start_year','period_end_year'])
            csvwriter.writerow([study_year,'1',study_year,study_year+1])
        
        # initialize the temporal structure files
        horizons_file = open(os.path.join(case_name,'temporal','horizon_params.csv'),'w',newline='')
        horizons_writer = csv.writer(horizons_file)
        header = ['subproblem_id','balancing_type_horizon','horizon','boundary']
        horizons_writer.writerow(header)
        
        horizon_timepoints_file = open(os.path.join(case_name,'temporal','horizon_timepoints.csv'),'w',newline='')
        horizon_timepoints_writer = csv.writer(horizon_timepoints_file)
        header = ['subproblem_id','stage_id','balancing_type_horizon','horizon','tmp_start','tmp_end']
        horizon_timepoints_writer.writerow(header)
        
        structure_file = open(os.path.join(case_name,'temporal','structure.csv'),'w',newline='')
        structure_writer = csv.writer(structure_file)
        header = ['subproblem_id','stage_id','timepoint','period','number_of_hours_in_timepoint','timepoint_weight','previous_stage_timepoint_map','spinup_or_lookahead','linked_timepoint','month','hour_of_day','timestamp','ignore_horizon_day']
        structure_writer.writerow(header)
        
        N_timepoints = len(timepoint_sub)
        N_horizons = np.max(tp_horizon_sub)
        N_digits = len(str(iterations))
        if weather_mode == 'Synchronized':
            iter_tmp = iterations
        elif weather_mode == 'MonteCarlo':
            iter_tmp = 1
        else:
            print('Error - weather mode not recognized') 
        for k in range(iter_tmp):
            for i in range(N_horizons):
                month_start = month_print[i*168]
                month_end = month_print[i*168+167]
                if month_start in months or month_end in months:
                    if weather_mode == 'Synchronized':
                        timepoint_start = ''.join((str(k+1).zfill(N_digits),str(timepoint_sub[i*168])))
                        timepoint_end = ''.join((str(k+1).zfill(N_digits),str(timepoint_sub[i*168+167])))
                    elif weather_mode == 'MonteCarlo':
                        timepoint_start = str(timepoint_sub[i*168])
                        timepoint_end = str(timepoint_sub[i*168+167])
                    else:
                        print('Error - weather mode not recognized') 
                    #horizon = k*N_horizons + tp_horizon_sub[i*168]
                    horizons_writer.writerow([k*N_horizons + tp_horizon_sub[i*168],'week',k*N_horizons + tp_horizon_sub[i*168],'circular'])
                    horizon_timepoints_writer.writerow([k*N_horizons + tp_horizon_sub[i*168],'1','week',k*N_horizons + tp_horizon_sub[i*168],timepoint_start,timepoint_end])
                    print_flag[i] = 1
                    for j in range(168):
                        if weather_mode == 'Synchronized':
                            timepoint = ''.join((str(k+1).zfill(N_digits),str(timepoint_sub[i*168 + j])))
                        elif weather_mode == 'MonteCarlo':
                            timepoint = str(timepoint_sub[i*168 + j])
                        else:
                            print('Error - weather mode not recognized') 
                        structure_writer.writerow([k*N_horizons + tp_horizon_sub[i*168],'1',timepoint,study_year,'1','1','','0','',month_print[i*168 + j],HE_print[i*168 + j]])
    
        structure_file.close()
        horizons_file.close()
        horizon_timepoints_file.close()
        

    elif output_mode == 'files':
        
        if weather_mode == 'Synchronized':
            FO_iterations = iterations
        elif weather_mode == 'MonteCarlo':
            FO_iterations = 1
        else:
            print('Error - weather mode not recognized') 
            
        # break drawns into batches for parallel processing
        N_batch_draws = int(np.ceil(N_draws/no_jobs))
        
        jobs = []
        for n in range(no_jobs):
            
            draw_list = range(n*N_batch_draws,min(N_draws,(n+1)*N_batch_draws))
            p = threading.Thread(target=print_temporal_files,args=(case_name,vartypes,draw_list,FO_iterations,timepoint_sub,tp_horizon_sub,months,month_print,weather_mode,study_year,))
            jobs.append(p)
            p.start()
        
        for job in jobs:
            job.join()
        
        # populate print_flag_h based on which draw directories exist
        print
        for j in range(N_draws):
            if os.path.exists(os.path.join(case_name,str(j+1),'inputs')):
                print_flag[j] = 1
        

    else:
        print('Error - output mode not recognized')
    
    print(''.join(('  Memory usage: ',str(int(Process().memory_info().rss/1000000)),' MB')))   
    
    ###########################################################################
    # Run simulation
    ###########################################################################
    
    print('simulating loads and resources...')
    
   
    # sort the aggregations in order of descending number of parameters for which forced outages are simulated (this seems to be rate-limiting)
    N_aggregations = len(aggregations[0])
    N_FO_agg = np.zeros(N_aggregations)
    for p in parameters[1]:
        if p.unitModel != 'NA':
            agg_ind = aggregations[0].index(p.aggregation)
            N_FO_agg[agg_ind] += 1
    agg_inds_sorted = np.argsort(-N_FO_agg)
    
    
    pool = multiprocessing.Pool(processes=no_jobs,maxtasksperchild=1)
    for i in range(N_aggregations):
        pool.apply_async(simulate_aggregation,args=(aggregations[1][agg_inds_sorted[i]],case_name,study_year,timepoint_sub,parameters,vartypes,timeseries,weather_mode,iterations,print_flag,output_mode,))
    pool.close()
    pool.join()
    
    
    ###########################################################################
    # Finalize input files for GridPath
    ###########################################################################
    
    print('finalizing input files for GridPath...')
    
    # consolidate inputs into single file where needed
    
        
    if output_mode == 'db':
        
        for k in range(len(vartypes[0])):
            
            if vartypes[1][k].print_mode == 'append file':
            
                filename_tmp = os.path.join(case_name,vartypes[1][k].filename,''.join(('1_',case_name,'_',vartypes[1][k].name,'.tab')))
                if os.path.exists(filename_tmp) == False:
                    with open(filename_tmp,'w',newline='') as appended_file:
                        appended_out = csv.writer(appended_file)
                    
                        # loop through the output files
                        i = 0
                        for file in glob.glob(os.path.join(case_name,vartypes[1][k].filename,'tmp','*')):
                            with open(file) as csvfile:
                                csvreader = csv.reader(csvfile)
                                if i > 0:
                                    csvreader.__next__()
                                else:
                                    appended_out.writerow(csvreader.__next__())
                                for row in csvreader:
                                    appended_out.writerow(row)
                            i += 1
                    
                    # delete temporary files
                    if os.path.exists(os.path.join(case_name,vartypes[1][k].filename,'tmp')):
                        shutil.rmtree(os.path.join(case_name,vartypes[1][k].filename,'tmp'))
            
    elif output_mode == 'files':
        
        jobs = []
        for n in range(no_jobs):
            
            draw_list = range(n*N_batch_draws,min(N_draws,(n+1)*N_batch_draws))
            p = threading.Thread(target=consolidate_files,args=(case_name,vartypes,draw_list,))
            jobs.append(p)
            p.start()
        
        for job in jobs:
            job.join()
        

        # copy common files
        case_dir = os.path.join('common_files',common_files,'case')
        for file in os.listdir(case_dir):
            shutil.copy(os.path.join(case_dir,file),os.path.join(case_name,file))
        for j in range(N_draws):
            draw_dir = os.path.join('common_files',common_files,'draws')
            for file in os.listdir(draw_dir):
                shutil.copy(os.path.join(draw_dir,file),os.path.join(case_name,str(j+1),'inputs',file))
        
        # revise scenario_description to reflect case name
        scen_file = open(os.path.join(case_name,'scenario_description.csv'),'w',newline='')
        scen_writer = csv.writer(scen_file)
        with open(os.path.join(case_name,'scenario_description_base.csv')) as basefile:
            csvreader = csv.reader(basefile)
            for row in csvreader:
                if row[1] == 'case_name':
                    scen_writer.writerow([row[0],case_name])
                else:
                    scen_writer.writerow(row)
        scen_file.close()
        os.remove(os.path.join(case_name,'scenario_description_base.csv'))
        
    print('simulation complete.')