from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Category, Product


class ProductSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Product.objects.filter(is_active=True).order_by('-updated_at')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse('catalog:detail', args=[obj.pk])


class CategorySitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.6

    def items(self):
        return Category.objects.filter(is_active=True).select_related('parent').order_by('display_order', 'name')

    def location(self, obj):
        if obj.parent is None:
            return reverse('catalog:category', args=[obj.slug])
        return reverse('catalog:product_list', args=[obj.parent.slug, obj.slug])


class StaticSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return ['catalog:hub']

    def location(self, item):
        return reverse(item)
