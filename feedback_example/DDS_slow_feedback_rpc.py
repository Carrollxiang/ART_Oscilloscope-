# import re
# import rpyc
# import threading
# import time
# from queue import Queue, Empty
# from collections import defaultdict
#
# import logging
#
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)
#
#
# class AD9910ConnectionPool:
#     """
#     AD9910 RPyC连接池管理器
#     """
#
#     def __init__(self, max_connections=10):
#         # 为每个服务器地址维护一个连接池
#         self.pools = defaultdict(Queue)
#         self.max_connections = max_connections
#         self.locks = defaultdict(threading.Lock)
#         self.active_connections = defaultdict(set)
#         # 记录连接创建时间，用于检测长时间未使用的连接
#         self.connection_times = {}
#
#     def _create_connection(self, ip_addr, port):
#         """
#         创建一个新的AD9910 RPyC连接
#         """
#         try:
#             conn = rpyc.connect(ip_addr, port, config={
#                 'allow_public_attrs': True,
#                 'allow_pickle': True,
#                 'allow_all_attrs': True
#             })
#             logger.debug(f"Created new connection to {ip_addr}:{port}")
#             # 记录连接创建时间
#             self.connection_times[conn] = time.time()
#             return conn
#         except Exception as e:
#             logger.error(f"Failed to create connection to {ip_addr}:{port}: {e}")
#             return None
#
#     def _is_connection_valid(self, conn):
#         """
#         检查连接是否有效
#         """
#         if not conn:
#             return False
#
#         try:
#             # 检查连接是否已关闭
#             if conn.closed:
#                 return False
#
#             # 尝试执行一个简单操作来验证连接
#             # 使用hasattr检查是否存在ping方法，避免直接调用引发异常
#             if hasattr(conn, 'ping'):
#                 conn.ping()
#             else:
#                 # 如果没有ping方法，尝试访问一个基本属性
#                 _ = conn.root
#             return True
#         except Exception as e:
#             logger.debug(f"Connection validation failed: {e}")
#             return False
#
#     def get_connection(self, ip_addr, port):
#         """
#         从连接池获取一个连接
#         """
#         server_key = (ip_addr, port)
#
#         # 尝试从连接池获取现有连接
#         while True:
#             try:
#                 conn = self.pools[server_key].get_nowait()
#                 logger.debug(f"Retrieved connection from pool for {ip_addr}:{port}")
#
#                 # 检查连接是否仍然有效
#                 if self._is_connection_valid(conn):
#                     logger.debug(f"Reusing existing connection to {ip_addr}:{port}")
#                     return conn
#                 else:
#                     # 连接已失效，关闭它并继续尝试获取下一个连接
#                     try:
#                         conn.close()
#                         if conn in self.connection_times:
#                             del self.connection_times[conn]
#                     except:
#                         pass
#                     logger.debug(f"Discarded invalid connection to {ip_addr}:{port}")
#             except Empty:
#                 # 连接池为空，创建新连接
#                 logger.debug(f"No available connection in pool for {ip_addr}:{port}, creating new one")
#                 return self._create_connection(ip_addr, port)
#
#     def return_connection(self, conn, ip_addr, port):
#         """
#         将连接返回到连接池
#         """
#         if not conn:
#             return
#
#         server_key = (ip_addr, port)
#
#         # 检查连接是否有效再决定是否放回池中
#         if self._is_connection_valid(conn):
#             # 检查连接池大小
#             if self.pools[server_key].qsize() < self.max_connections:
#                 self.pools[server_key].put(conn)
#                 logger.debug(f"Returned connection to pool for {ip_addr}:{port}")
#             else:
#                 # 池已满，直接关闭连接
#                 try:
#                     conn.close()
#                     if conn in self.connection_times:
#                         del self.connection_times[conn]
#                     logger.debug(f"Pool full, closed connection to {ip_addr}:{port}")
#                 except:
#                     pass
#         else:
#             # 连接已失效，直接关闭
#             try:
#                 conn.close()
#                 if conn in self.connection_times:
#                     del self.connection_times[conn]
#                 logger.debug(f"Closed invalid connection to {ip_addr}:{port}")
#             except:
#                 pass
#
#     def close_all_connections(self):
#         """
#         关闭所有连接
#         """
#         for server_key, pool in self.pools.items():
#             while not pool.empty():
#                 try:
#                     conn = pool.get_nowait()
#                     if conn and not conn.closed:
#                         conn.close()
#                         if conn in self.connection_times:
#                             del self.connection_times[conn]
#                 except Empty:
#                     break
#         # 清空连接时间记录
#         self.connection_times.clear()
#         logger.info("Closed all connections in pool")
#
#
# # 全局连接池实例
# connection_pool = AD9910ConnectionPool(max_connections=5)
#
#
# def ad9910_rpc(ip_addr, port, ad9910_id, prof, freq=None, amp=None, phase=None, delta_amp=None):
#     """
#     使用连接池的AD9910 RPC调用
#     """
#     conn = None
#     try:
#         # 从连接池获取连接
#         conn = connection_pool.get_connection(ip_addr, port)
#         if not conn:
#             logger.error("Failed to get connection from pool")
#             return False
#
#         # 获取AD9910服务
#         ad9910_service = conn.root.get_ad9910_service()
#
#         result = True
#         if freq is not None:
#             # 设置频率
#             result &= ad9910_service.set_frequency(ad9910_id, prof, freq)
#             logger.info(f"Set frequency result: {result}")
#         if amp is not None:
#             # 设置幅度
#             result &= ad9910_service.set_amplitude(ad9910_id, prof, amp)
#             logger.info(f"Set amplitude result: {result}")
#         if phase is not None:
#             # 设置相位
#             result &= ad9910_service.set_phase(ad9910_id, prof, phase)
#             logger.info(f"Set phase result: {result}")
#         if delta_amp is not None:
#             result &= ad9910_service.adjust_amplitude(ad9910_id, prof, delta_amp)
#             logger.info(f"Adjust amplitude result: {result}")
#
#         return result
#
#     except Exception as e:
#         logger.error(f"Error during operation: {e}")
#         return False
#     finally:
#         # 将连接返回到连接池
#         if conn:
#             connection_pool.return_connection(conn, ip_addr, port)
#
#
# def slow_feedback(preset_value, value, kp, ki, kd, last_error, accumulate_error, target_server, SN, Prof):
#     if not target_server:
#         return
#     if not SN:
#         return
#     # if not Prof:
#     #     return
#
#     error = preset_value - value
#     pout = error * kp
#     dout = (error - last_error) * kd
#     accumulate_error.append(error)
#     iout = sum(accumulate_error) * ki
#     if iout > 0.1:
#         iout = 0.1
#     elif iout < -0.1:
#         iout = -0.1
#     out = pout + iout + dout
#     if out > 0.1:
#         out = 0.1
#     elif out < -0.1:
#         out = -0.1
#     if len(accumulate_error) > 10:
#         accumulate_error.pop(0)
#     if ki == 0:
#         for i in range(10):
#             accumulate_error.pop(0)
#             accumulate_error.append(0)
#     if abs(error) > 1:
#         return error
#
#     target_server = re.split('/', target_server)
#     target_id = target_server[0]
#     target_port = int(target_server[1])
#
#     # 使用连接池的RPC调用
#     success = ad9910_rpc(target_id, target_port, int("0x" + SN, 16), Prof,
#                          freq=None, amp=None, phase=None, delta_amp=out)
#     if not success:
#         logger.error("Failed to execute AD9910 RPC call")
#
#     return error
#
#
# def cleanup_connection_pool():
#     """
#     清理连接池，关闭所有连接
#     在程序结束时调用此函数
#     """
#     connection_pool.close_all_connections()
#
#
# if __name__ == '__main__':
#     # test pushout
#     try:
#         error = slow_feedback(0.6, 0.8, 0.03, 0, 0, 0, [], "192.168.1.20/3251", "0842", 0)
#         print(f"Error: {error}")
#     finally:
#         # 清理连接池
#         cleanup_connection_pool()


import re
import rpyc
import threading
import time
import json
from queue import Queue, Empty
from collections import defaultdict

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AD9910ConnectionPool:
    """
    AD9910 RPyC连接池管理器
    """
    
    def __init__(self, max_connections=10):
        # 为每个服务器地址维护一个连接池
        self.pools = defaultdict(Queue)
        self.max_connections = max_connections
        self.locks = defaultdict(threading.Lock)
        self.active_connections = defaultdict(set)
        # 记录连接创建时间，用于检测长时间未使用的连接
        self.connection_times = {}
        # 连接统计信息
        self.stats = {
            'connections_created': 0,
            'connections_reused': 0,
            'connections_failed': 0,
            'average_connection_time': 0.0,
            'total_connection_time': 0.0
        }
    
    def _create_connection(self, ip_addr, port):
        """
        创建一个新的AD9910 RPyC连接
        """
        start_time = time.time()
        try:
            conn = rpyc.connect(ip_addr, port, config={
                'allow_public_attrs': True,
                'allow_pickle': True,
                'allow_all_attrs': True
            })
            end_time = time.time()
            connection_time = end_time - start_time
            
            # 更新统计信息
            self.stats['connections_created'] += 1
            self.stats['total_connection_time'] += connection_time
            self.stats['average_connection_time'] = (
                    self.stats['total_connection_time'] / self.stats['connections_created']
            )
            
            logger.debug(f"Created new connection to {ip_addr}:{port} in {connection_time:.3f}s")
            # 记录连接创建时间
            self.connection_times[conn] = time.time()
            return conn
        except Exception as e:
            end_time = time.time()
            connection_time = end_time - start_time
            logger.error(f"Failed to create connection to {ip_addr}:{port} in {connection_time:.3f}s: {e}")
            self.stats['connections_failed'] += 1
            return None
    
    def _is_connection_valid(self, conn):
        """
        检查连接是否有效
        """
        if not conn:
            return False
        
        try:
            # 检查连接是否已关闭
            if conn.closed:
                return False
            
            # 尝试执行一个简单操作来验证连接
            # 使用hasattr检查是否存在ping方法，避免直接调用引发异常
            if hasattr(conn, 'ping'):
                conn.ping()
            else:
                # 如果没有ping方法，尝试访问一个基本属性
                _ = conn.root
            return True
        except Exception as e:
            logger.debug(f"Connection validation failed: {e}")
            return False
    
    def get_connection(self, ip_addr, port):
        """
        从连接池获取一个连接
        """
        server_key = (ip_addr, port)
        
        # 尝试从连接池获取现有连接
        while True:
            try:
                conn = self.pools[server_key].get_nowait()
                logger.debug(f"Retrieved connection from pool for {ip_addr}:{port}")
                
                # 检查连接是否仍然有效
                if self._is_connection_valid(conn):
                    self.stats['connections_reused'] += 1
                    logger.debug(f"Reusing existing connection to {ip_addr}:{port}")
                    return conn
                else:
                    # 连接已失效，关闭它并继续尝试获取下一个连接
                    try:
                        conn.close()
                        if conn in self.connection_times:
                            del self.connection_times[conn]
                    except:
                        pass
                    logger.debug(f"Discarded invalid connection to {ip_addr}:{port}")
            except Empty:
                # 连接池为空，创建新连接
                logger.debug(f"No available connection in pool for {ip_addr}:{port}, creating new one")
                return self._create_connection(ip_addr, port)
    
    def return_connection(self, conn, ip_addr, port):
        """
        将连接返回到连接池
        """
        if not conn:
            return
        
        server_key = (ip_addr, port)
        
        # 检查连接是否有效再决定是否放回池中
        if self._is_connection_valid(conn):
            # 检查连接池大小
            if self.pools[server_key].qsize() < self.max_connections:
                self.pools[server_key].put(conn)
                logger.debug(f"Returned connection to pool for {ip_addr}:{port}")
            else:
                # 池已满，直接关闭连接
                try:
                    conn.close()
                    if conn in self.connection_times:
                        del self.connection_times[conn]
                    logger.debug(f"Pool full, closed connection to {ip_addr}:{port}")
                except:
                    pass
        else:
            # 连接已失效，直接关闭
            try:
                conn.close()
                if conn in self.connection_times:
                    del self.connection_times[conn]
                logger.debug(f"Closed invalid connection to {ip_addr}:{port}")
            except:
                pass
    
    def close_all_connections(self):
        """
        关闭所有连接
        """
        for server_key, pool in self.pools.items():
            while not pool.empty():
                try:
                    conn = pool.get_nowait()
                    if conn and not conn.closed:
                        conn.close()
                        if conn in self.connection_times:
                            del self.connection_times[conn]
                except Empty:
                    break
        # 清空连接时间记录
        self.connection_times.clear()
        logger.info("Closed all connections in pool")
    
    def get_pool_stats(self):
        """
        获取连接池统计信息
        """
        stats = self.stats.copy()
        # 添加当前池状态
        pool_info = {}
        total_connections = 0
        for server_key, pool in self.pools.items():
            pool_size = pool.qsize()
            pool_info[f"{server_key[0]}:{server_key[1]}"] = pool_size
            total_connections += pool_size
        
        stats['current_pool_size'] = total_connections
        stats['pool_details'] = pool_info
        return stats
    
    def print_pool_stats(self):
        """
        打印连接池统计信息
        """
        stats = self.get_pool_stats()
        logger.info("=== Connection Pool Statistics ===")
        logger.info(f"Connections created: {stats['connections_created']}")
        logger.info(f"Connections reused: {stats['connections_reused']}")
        logger.info(f"Connections failed: {stats['connections_failed']}")
        logger.info(f"Average connection time: {stats['average_connection_time']:.3f}s")
        logger.info(f"Current pool size: {stats['current_pool_size']}")
        logger.info("Pool details:")
        for server, count in stats['pool_details'].items():
            logger.info(f"  {server}: {count} connections")
        logger.info("================================")


# 全局连接池实例
connection_pool = AD9910ConnectionPool(max_connections=5)


def ad9910_rpc(ip_addr, port, ad9910_id, prof, freq=None, amp=None, phase=None, delta_amp=None):
    """
    使用连接池的AD9910 RPC调用
    """
    conn = None
    try:
        # 从连接池获取连接
        conn = connection_pool.get_connection(ip_addr, port)
        if not conn:
            logger.error("Failed to get connection from pool")
            return False
        
        # 获取AD9910服务
        ad9910_service = conn.root.get_ad9910_service()
        
        result = True
        if freq is not None:
            # 设置频率
            result &= ad9910_service.set_frequency(ad9910_id, prof, freq)
            logger.info(f"Set frequency result: {result}")
        if amp is not None:
            # 设置幅度
            result &= ad9910_service.set_amplitude(ad9910_id, prof, amp)
            logger.info(f"Set amplitude result: {result}")
        if phase is not None:
            # 设置相位
            result &= ad9910_service.set_phase(ad9910_id, prof, phase)
            logger.info(f"Set phase result: {result}")
        if delta_amp is not None:
            result &= ad9910_service.adjust_amplitude(ad9910_id, prof, delta_amp)
            logger.info(f"Adjust amplitude result: {result}")
        
        return result
    
    except Exception as e:
        logger.error(f"Error during operation: {e}")
        return False
    finally:
        # 将连接返回到连接池
        if conn:
            connection_pool.return_connection(conn, ip_addr, port)


def slow_feedback(preset_value, value, kp, ki, kd, last_error, accumulate_error, target_server, SN, Prof):
    if not target_server:
        return
    if not SN:
        return
    # if not Prof:
    #     return
    
    error = preset_value - value
    pout = error * kp
    dout = (error - last_error) * kd
    accumulate_error.append(error)
    iout = sum(accumulate_error) * ki
    if iout > 0.1:
        iout = 0.1
    elif iout < -0.1:
        iout = -0.1
    out = pout + iout + dout
    if out > 0.1:
        out = 0.1
    elif out < -0.1:
        out = -0.1
    if len(accumulate_error) > 10:
        accumulate_error.pop(0)
    if ki == 0:
        for i in range(10):
            accumulate_error.pop(0)
            accumulate_error.append(0)
    if abs(error) > 1:
        return error
    
    target_server = re.split('/', target_server)
    target_id = target_server[0]
    target_port = int(target_server[1])
    
    # print(len(SN))
    
    if len(SN) == 4:
        # 使用连接池的RPC调用
        conn = None
        try:
            # 获取连接以发送连接池信息
            conn = connection_pool.get_connection(target_id, target_port)
            if conn:
                # 获取AD9910服务
                ad9910_service = conn.root.get_ad9910_service()
                
                # # 获取并发送连接池统计信息到服务器
                # pool_stats = connection_pool.get_pool_stats()
                # stats_message = json.dumps(pool_stats, indent=2)
                # ad9910_service.client_print(f"Connection Pool Stats:\n{stats_message}")
                
                # 执行主要的AD9910控制操作
                success = ad9910_service.adjust_amplitude(int("0x" + SN, 16), Prof, out)
                if not success:
                    logger.error("Failed to execute AD9910 RPC call")
            else:
                logger.error("Failed to get connection for sending pool stats")
        except Exception as e:
            logger.error(f"Error sending pool stats or executing operation: {e}")
        finally:
            # 将连接返回到连接池
            if conn:
                connection_pool.return_connection(conn, target_id, target_port)
    elif len(SN) == 1:
        conn = rpyc.connect(target_id, target_port)  # 替换为实际的服务端IP地址
        RWG_info = conn.root.get_rwg_info()
        pwr_0 = RWG_info[int(SN)]['sbg_freq'][Prof][1]
        conn.root.change_rwg_info(card=int(SN), sbg_ch=Prof, amp=pwr_0+out)
        conn.close()
    
    return error


def print_connection_pool_stats():
    """
    打印连接池统计信息的公共函数
    """
    connection_pool.print_pool_stats()


def cleanup_connection_pool():
    """
    清理连接池，关闭所有连接
    在程序结束时调用此函数
    """
    connection_pool.close_all_connections()


if __name__ == '__main__':
    # test pushout
    try:
        # # DDS测试
        # error = slow_feedback(0.8, 0.5, 0.03, 0, 0, 0, [], "192.168.1.20/3251", "0D11", 0)
        # print(f"Error: {error}")
        # 白盒子测试
        error = slow_feedback(0.8, 0.5, 0.03, 0, 0, 0, [], "192.168.1.115/18861", "3", 0x00)
        print(f"Error: {error}")
        # 打印连接池状态
        print_connection_pool_stats()
    finally:
        # 清理连接池
        cleanup_connection_pool()
        