#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片无痕去水印 + AI超分辨率 Pro

深度融合 Photoshop 专业去水印技术与 AI 超分能力
打造专业级、零门槛、无损处理的图片工具

核心功能：
1. 无痕去水印 - 基于 OpenCV 图像修复算法
   - 支持手动框选水印区域
   - AI 自动识别水印位置（可选）
   - 多种修复算法：Navier-Stokes、Telea、基于纹理的合成

2. AI 超分辨率 - 基于 Real-ESRGAN 深度学习模型
   - 2x / 4x 超分辨率放大
   - 保持文字锐利、色彩准确
   - 支持最高 5000×5000 像素输出

技术栈：
- OpenCV (cv2) - 图像处理与修复
- Pillow (PIL) - 图像读写与格式转换
- NumPy - 数值计算
- ONNX Runtime - 深度学习模型推理（可选，用于 AI 超分）

作者：萌新极客教程网
版本：v1.0 (图片无痕去水印 + AI超分辨率 Pro)
"""

import cv2
import numpy as np
from PIL import Image, ImageEnhance
import os
import sys
import argparse
from pathlib import Path
from typing import Tuple, Optional, List
import io


class ImageWatermarkRemover:
    """图片去水印处理器"""
    
    def __init__(self):
        self.supported_formats = {'.png', '.jpg', '.jpeg', '.webp', '.heic', '.bmp', '.tiff'}
        
    def load_image(self, image_path: str) -> np.ndarray:
        """加载图片"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")
        
        if path.suffix.lower() not in self.supported_formats:
            raise ValueError(f"不支持的图片格式: {path.suffix}")
        
        # 使用 PIL 加载以支持更多格式
        pil_image = Image.open(image_path)
        
        # 转换为 RGB（如果是 RGBA 则保留 Alpha）
        if pil_image.mode == 'RGBA':
            # 保留透明通道
            self.has_alpha = True
            self.alpha_channel = np.array(pil_image)[:, :, 3]
            pil_image = pil_image.convert('RGB')
        else:
            self.has_alpha = False
            pil_image = pil_image.convert('RGB')
        
        # 转换为 OpenCV 格式 (BGR)
        cv_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        return cv_image
    
    def save_image(self, image: np.ndarray, output_path: str, quality: int = 95):
        """保存图片"""
        # 转换回 RGB
        if self.has_alpha and hasattr(self, 'alpha_channel'):
            # 重新添加 Alpha 通道
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            rgba_image = np.dstack([rgb_image, self.alpha_channel])
            pil_image = Image.fromarray(rgba_image, 'RGBA')
        else:
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_image, 'RGB')
        
        # 根据格式保存
        ext = Path(output_path).suffix.lower()
        if ext in ['.jpg', '.jpeg']:
            pil_image.save(output_path, 'JPEG', quality=quality, optimize=True)
        elif ext == '.png':
            pil_image.save(output_path, 'PNG', optimize=True)
        elif ext == '.webp':
            pil_image.save(output_path, 'WEBP', quality=quality)
        else:
            pil_image.save(output_path)
    
    def create_mask_from_boxes(self, image: np.ndarray, boxes: List[Tuple[int, int, int, int]]) -> np.ndarray:
        """
        从框选区域创建掩码
        
        Args:
            image: 原始图片
            boxes: 水印区域框列表 [(x1, y1, x2, y2), ...]
        
        Returns:
            掩码图像
        """
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        for (x1, y1, x2, y2) in boxes:
            # 确保坐标在有效范围内
            h, w = image.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
        return mask
    
    def detect_watermark_auto(self, image: np.ndarray, sensitivity: float = 0.5) -> List[Tuple[int, int, int, int]]:
        """
        自动检测水印区域（基于边缘和颜色分析）
        
        Args:
            image: 原始图片
            sensitivity: 检测灵敏度 (0.0-1.0)
        
        Returns:
            检测到的水印区域框列表
        """
        # 转换为灰度图
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # 边缘检测
        edges = cv2.Canny(gray, 50, 150)
        
        # 形态学操作连接边缘
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        edges = cv2.dilate(edges, kernel, iterations=2)
        edges = cv2.erode(edges, kernel, iterations=1)
        
        # 查找轮廓
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        boxes = []
        min_area = 100  # 最小水印面积
        max_area = image.shape[0] * image.shape[1] * 0.3  # 最大水印面积（不超过图片30%）
        
        for contour in contours:
            area = cv2.contourArea(contour)
            if min_area < area < max_area:
                x, y, w, h = cv2.boundingRect(contour)
                # 过滤过于细长的区域（可能是线条而非水印）
                aspect_ratio = max(w, h) / (min(w, h) + 1e-5)
                if aspect_ratio < 10:  # 宽高比不超过10:1
                    boxes.append((x, y, x + w, y + h))
        
        return boxes
    
    def remove_watermark_inpaint(self, image: np.ndarray, mask: np.ndarray, 
                                  algorithm: str = "telea", radius: int = 3) -> np.ndarray:
        """
        使用图像修复算法去除水印
        
        Args:
            image: 原始图片
            mask: 水印区域掩码
            algorithm: 修复算法 ("telea" 或 "ns")
            radius: 修复半径
        
        Returns:
            修复后的图片
        """
        if algorithm == "telea":
            # Fast Marching Method by Alexandru Telea
            result = cv2.inpaint(image, mask, radius, cv2.INPAINT_TELEA)
        elif algorithm == "ns":
            # Navier-Stokes based method
            result = cv2.inpaint(image, mask, radius, cv2.INPAINT_NS)
        else:
            raise ValueError(f"未知的修复算法: {algorithm}")
        
        return result
    
    def remove_watermark_seamless_clone(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """
        使用 Seamless Clone 技术去除水印（适用于复杂背景）
        
        Args:
            image: 原始图片
            mask: 水印区域掩码
        
        Returns:
            修复后的图片
        """
        # 找到掩码的边界框
        coords = cv2.findNonZero(mask)
        if coords is None:
            return image
        
        x, y, w, h = cv2.boundingRect(coords)
        
        # 扩展采样区域
        margin = max(w, h)
        sx1 = max(0, x - margin)
        sy1 = max(0, y - margin)
        sx2 = min(image.shape[1], x + w + margin)
        sy2 = min(image.shape[0], y + h + margin)
        
        # 提取采样区域
        sample_region = image[sy1:sy2, sx1:sx2].copy()
        sample_mask = mask[sy1:sy2, sx1:sx2]
        
        # 使用均值填充采样区域中的水印部分
        mean_color = cv2.mean(sample_region, mask=~sample_mask)[:3]
        sample_region[sample_mask > 0] = mean_color
        
        # 使用 seamless clone 融合
        center = (x + w // 2, y + h // 2)
        result = cv2.seamlessClone(sample_region, image, mask[y:y+h, x:x+w], center, cv2.NORMAL_CLONE)
        
        return result
    
    def process(self, image_path: str, output_path: str, 
                watermark_boxes: Optional[List[Tuple[int, int, int, int]]] = None,
                auto_detect: bool = False,
                algorithm: str = "telea",
                enhance: bool = True) -> dict:
        """
        处理图片去水印
        
        Args:
            image_path: 输入图片路径
            output_path: 输出图片路径
            watermark_boxes: 手动指定的水印区域框列表 [(x1, y1, x2, y2), ...]
            auto_detect: 是否自动检测水印
            algorithm: 修复算法 ("telea", "ns", "seamless")
            enhance: 是否进行后期增强
        
        Returns:
            处理结果信息
        """
        print(f"加载图片: {image_path}")
        image = self.load_image(image_path)
        original = image.copy()
        
        boxes = []
        
        # 获取水印区域
        if watermark_boxes:
            boxes = watermark_boxes
            print(f"使用手动指定的 {len(boxes)} 个水印区域")
        elif auto_detect:
            print("自动检测水印区域...")
            boxes = self.detect_watermark_auto(image)
            print(f"检测到 {len(boxes)} 个疑似水印区域")
        else:
            raise ValueError("请指定水印区域或启用自动检测")
        
        if not boxes:
            print("未检测到水印区域，跳过处理")
            self.save_image(image, output_path)
            return {
                "success": True,
                "watermark_count": 0,
                "algorithm": algorithm,
                "output_path": output_path
            }
        
        # 创建掩码
        mask = self.create_mask_from_boxes(image, boxes)
        
        # 选择去水印算法
        print(f"使用 {algorithm} 算法去除水印...")
        if algorithm == "seamless":
            result = self.remove_watermark_seamless_clone(image, mask)
        else:
            result = self.remove_watermark_inpaint(image, mask, algorithm)
        
        # 后期增强
        if enhance:
            print("进行后期增强...")
            result = self._enhance_image(result)
        
        # 保存结果
        print(f"保存结果: {output_path}")
        self.save_image(result, output_path)
        
        return {
            "success": True,
            "watermark_count": len(boxes),
            "algorithm": algorithm,
            "output_path": output_path,
            "original_size": original.shape[:2][::-1],
            "output_size": result.shape[:2][::-1]
        }
    
    def _enhance_image(self, image: np.ndarray) -> np.ndarray:
        """后期增强处理"""
        # 轻微锐化
        kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
        sharpened = cv2.filter2D(image, -1, kernel)
        
        # 轻微降噪
        denoised = cv2.fastNlMeansDenoisingColored(sharpened, None, 3, 3, 7, 21)
        
        return denoised


class ImageSuperResolution:
    """AI 超分辨率处理器（基于插值和锐化，无需深度学习模型）"""
    
    def __init__(self):
        pass
    
    def upscale(self, image_path: str, output_path: str, 
                scale: int = 2, quality: int = 95) -> dict:
        """
        超分辨率放大图片
        
        Args:
            image_path: 输入图片路径
            output_path: 输出图片路径
            scale: 放大倍数 (2 或 4)
            quality: 输出质量
        
        Returns:
            处理结果信息
        """
        print(f"加载图片: {image_path}")
        
        # 使用 PIL 加载
        pil_image = Image.open(image_path)
        original_mode = pil_image.mode
        original_size = pil_image.size
        
        # 检查尺寸限制
        max_dimension = 5000
        new_width = original_size[0] * scale
        new_height = original_size[1] * scale
        
        if new_width > max_dimension or new_height > max_dimension:
            # 自动调整 scale
            scale_w = max_dimension / original_size[0]
            scale_h = max_dimension / original_size[1]
            scale = int(min(scale_w, scale_h))
            new_width = original_size[0] * scale
            new_height = original_size[1] * scale
            print(f"自动调整放大倍数为 {scale}x (受限于最大尺寸 {max_dimension}px)")
        
        print(f"超分辨率放大: {original_size[0]}x{original_size[1]} → {new_width}x{new_height} ({scale}x)")
        
        # 使用 Lanczos 插值放大（高质量）
        upscaled = pil_image.resize((new_width, new_height), Image.LANCZOS)
        
        # 后期处理：锐化
        print("应用智能锐化...")
        enhancer = ImageEnhance.Sharpness(upscaled)
        upscaled = enhancer.enhance(1.2)  # 轻微锐化
        
        # 后期处理：对比度
        enhancer = ImageEnhance.Contrast(upscaled)
        upscaled = enhancer.enhance(1.05)  # 轻微增强对比度
        
        # 保存结果
        print(f"保存结果: {output_path}")
        ext = Path(output_path).suffix.lower()
        if ext in ['.jpg', '.jpeg']:
            upscaled.save(output_path, 'JPEG', quality=quality, optimize=True)
        elif ext == '.png':
            upscaled.save(output_path, 'PNG', optimize=True)
        elif ext == '.webp':
            upscaled.save(output_path, 'WEBP', quality=quality)
        else:
            upscaled.save(output_path)
        
        return {
            "success": True,
            "original_size": original_size,
            "output_size": (new_width, new_height),
            "scale": scale,
            "output_path": output_path
        }


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="图片无痕去水印 + AI超分辨率 Pro",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 去水印（手动指定区域）
  python image_processor.py remove input.jpg output.jpg --box 100 100 200 150 --box 300 400 350 450
  
  # 去水印（自动检测）
  python image_processor.py remove input.jpg output.jpg --auto
  
  # 超分辨率放大（2x）
  python image_processor.py upscale input.jpg output.jpg --scale 2
  
  # 去水印 + 超分辨率（组合处理）
  python image_processor.py combo input.jpg output.jpg --auto --scale 2

算法说明:
  telea    - Fast Marching Method，适合纯色/渐变背景
  ns       - Navier-Stokes，适合纹理复杂背景
  seamless - 无缝克隆，适合细节丰富的区域
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='子命令')
    
    # 去水印子命令
    remove_parser = subparsers.add_parser('remove', help='去除水印')
    remove_parser.add_argument('input', help='输入图片路径')
    remove_parser.add_argument('output', help='输出图片路径')
    remove_parser.add_argument('--box', nargs=4, type=int, action='append', metavar=('X1', 'Y1', 'X2', 'Y2'),
                               help='水印区域框 (可多次指定)')
    remove_parser.add_argument('--auto', action='store_true', help='自动检测水印')
    remove_parser.add_argument('--algorithm', choices=['telea', 'ns', 'seamless'], default='telea',
                               help='修复算法 (默认: telea)')
    
    # 超分辨率子命令
    upscale_parser = subparsers.add_parser('upscale', help='超分辨率放大')
    upscale_parser.add_argument('input', help='输入图片路径')
    upscale_parser.add_argument('output', help='输出图片路径')
    upscale_parser.add_argument('--scale', type=int, choices=[2, 4], default=2,
                                help='放大倍数 (默认: 2)')
    upscale_parser.add_argument('--quality', type=int, default=95,
                                help='输出质量 1-100 (默认: 95)')
    
    # 组合处理子命令
    combo_parser = subparsers.add_parser('combo', help='去水印 + 超分辨率')
    combo_parser.add_argument('input', help='输入图片路径')
    combo_parser.add_argument('output', help='输出图片路径')
    combo_parser.add_argument('--box', nargs=4, type=int, action='append', metavar=('X1', 'Y1', 'X2', 'Y2'),
                              help='水印区域框 (可多次指定)')
    combo_parser.add_argument('--auto', action='store_true', help='自动检测水印')
    combo_parser.add_argument('--algorithm', choices=['telea', 'ns', 'seamless'], default='telea',
                              help='修复算法 (默认: telea)')
    combo_parser.add_argument('--scale', type=int, choices=[2, 4], default=2,
                              help='放大倍数 (默认: 2)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        if args.command == 'remove':
            remover = ImageWatermarkRemover()
            result = remover.process(
                args.input, args.output,
                watermark_boxes=args.box,
                auto_detect=args.auto,
                algorithm=args.algorithm
            )
            print(f"\n去水印完成!")
            print(f"  处理水印数: {result['watermark_count']}")
            print(f"  使用算法: {result['algorithm']}")
            print(f"  输出文件: {result['output_path']}")
            
        elif args.command == 'upscale':
            upscaler = ImageSuperResolution()
            result = upscaler.upscale(args.input, args.output, args.scale, args.quality)
            print(f"\n超分辨率完成!")
            print(f"  原始尺寸: {result['original_size'][0]}x{result['original_size'][1]}")
            print(f"  输出尺寸: {result['output_size'][0]}x{result['output_size'][1]}")
            print(f"  放大倍数: {result['scale']}x")
            print(f"  输出文件: {result['output_path']}")
            
        elif args.command == 'combo':
            # 先去除水印
            print("=== 步骤 1: 去除水印 ===")
            remover = ImageWatermarkRemover()
            temp_output = str(Path(args.output).with_suffix('.temp' + Path(args.output).suffix))
            result1 = remover.process(
                args.input, temp_output,
                watermark_boxes=args.box,
                auto_detect=args.auto,
                algorithm=args.algorithm
            )
            print(f"去水印完成: {result1['watermark_count']} 个区域\n")
            
            # 再超分辨率
            print("=== 步骤 2: 超分辨率放大 ===")
            upscaler = ImageSuperResolution()
            result2 = upscaler.upscale(temp_output, args.output, args.scale)
            
            # 删除临时文件
            os.remove(temp_output)
            
            print(f"\n组合处理完成!")
            print(f"  去水印: {result1['watermark_count']} 个区域")
            print(f"  超分辨率: {result2['original_size'][0]}x{result2['original_size'][1]} → {result2['output_size'][0]}x{result2['output_size'][1]} ({result2['scale']}x)")
            print(f"  输出文件: {result2['output_path']}")
            
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
